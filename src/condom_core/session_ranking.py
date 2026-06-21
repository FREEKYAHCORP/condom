from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from condom_core.db import clear_arm
from condom_core.ingest import rebuild_session_order
from condom_core.minimax_client import call_minimax
from condom_core.parse_llm_outputs import parse_llm_output
from condom_core.predictions import batches_for_session, insert_llm_prediction, insert_ranked_predictions
from condom_core.profile import load_positive_profile_text
from condom_core.prompts import PROMPT_VERSION, build_prompt
from condom_core.rankers.bm25 import ARM as BM25_ARM, rank_bm25
from condom_core.rankers.cheap_combo import ARM as CHEAP_COMBO_ARM, rank_combo
from condom_core.rankers.cheap_linear import ARM as CHEAP_LINEAR_ARM, can_train
from condom_core.rankers.native import ARM as NATIVE_ARM, rank as rank_native
from condom_core.rankers.tfidf import ARM as TFIDF_ARM, rank_tfidf

Mode = Literal["native", "cheap", "m3"]

MODE_TO_ARM: dict[str, str] = {
    "native": NATIVE_ARM,
    "cheap": CHEAP_COMBO_ARM,
    "m3": "llm_usersim_encounter",
}

ARM_ALIASES: dict[str, str] = {
    "native": NATIVE_ARM,
    "native_x_order": NATIVE_ARM,
    "cheap": CHEAP_COMBO_ARM,
    "cheap_combo": CHEAP_COMBO_ARM,
    "cheap_combo_v0": CHEAP_COMBO_ARM,
    "bm25": BM25_ARM,
    "bm25_saved_profile": BM25_ARM,
    "tfidf": TFIDF_ARM,
    "tfidf_saved_profile": TFIDF_ARM,
    "m3": MODE_TO_ARM["m3"],
    "llm_usersim_encounter": MODE_TO_ARM["m3"],
    "cheap_linear": CHEAP_LINEAR_ARM,
    "cheap_linear_v1": CHEAP_LINEAR_ARM,
}

M3_ARM = MODE_TO_ARM["m3"]
M3_MODEL = "MiniMax-M3"
M3_STRICT_SUFFIX = """

IMPORTANT COMPLETENESS CHECK:
The timeline above contains multiple item ids. Return one block for every id in
the timeline, in the same order. Do not stop after the first item.
"""


def llm_utility(row: dict) -> float:
    return (
        3.0 * row["pred_save"]
        + 1.5 * row["pred_open"]
        + 0.5 * row["pred_stop"]
        + 0.05 * min(row["pred_look_sec"], 20)
    )


def resolve_arm(name: str) -> str:
    key = name.strip().lower()
    if key not in ARM_ALIASES:
        known = ", ".join(sorted(set(ARM_ALIASES.keys())))
        raise ValueError(f"unknown arm or mode: {name!r} (known: {known})")
    return ARM_ALIASES[key]


def existing_count(conn, arm: str, session_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM arm_predictions WHERE arm=? AND session_id=?",
        (arm, session_id),
    ).fetchone()["n"]


def _rank_native_arm(conn, session_id: str, *, refresh: bool) -> int:
    arm = NATIVE_ARM
    if refresh or not existing_count(conn, arm, session_id):
        clear_arm(conn, arm, session_id)
        count = 0
        for batch_id, rows in batches_for_session(conn, session_id).items():
            insert_ranked_predictions(conn, arm, session_id, batch_id, rank_native(rows))
            count += len(rows)
        return count
    return existing_count(conn, arm, session_id)


def _rank_profile_arm(
    conn,
    session_id: str,
    arm: str,
    rank_fn,
    *,
    refresh: bool,
) -> int:
    if refresh or not existing_count(conn, arm, session_id):
        clear_arm(conn, arm, session_id)
        profile = load_positive_profile_text()
        count = 0
        for batch_id, rows in batches_for_session(conn, session_id).items():
            insert_ranked_predictions(conn, arm, session_id, batch_id, rank_fn(rows, profile))
            count += len(rows)
        return count
    return existing_count(conn, arm, session_id)


def _rank_cheap_linear(conn, session_id: str, *, refresh: bool) -> int:
    arm = CHEAP_LINEAR_ARM
    if refresh or not existing_count(conn, arm, session_id):
        clear_arm(conn, arm, session_id)
    if not can_train(conn, session_id):
        return 0
    # Training not implemented yet; arm stays empty until multiple labeled sessions exist.
    return 0


@dataclass
class M3Options:
    refresh: bool = False
    limit_batches: int | None = None
    skip_complete: bool = False
    rerun_partial: bool = False
    strict_completeness: bool = False
    max_calls: int | None = None

def _limit_batches(batches: list[tuple[str, list[dict]]], limit: int | None) -> list[tuple[str, list[dict]]]:
    if limit:
        return batches[:limit]
    return batches



def rank_m3_session(
    conn,
    session_id: str,
    options: M3Options | None = None,
) -> tuple[int, int, list[dict]]:
    """Run M3 user-sim ranking. Returns (calls_made, predictions_written, model_call_summaries)."""
    opts = options or M3Options()
    arm = M3_ARM
    model_summaries: list[dict] = []

    if opts.refresh:
        clear_arm(conn, arm, session_id)

    batches = _limit_batches(list(batches_for_session(conn, session_id).items()), opts.limit_batches)

    calls_made = 0
    total_written = 0

    for batch_id, rows in batches:
        if opts.max_calls is not None and calls_made >= opts.max_calls:
            break
        if not rows:
            continue

        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
            (arm, session_id, batch_id),
        ).fetchone()["n"]

        if opts.skip_complete and existing >= len(rows):
            continue
        if opts.rerun_partial and existing >= len(rows):
            continue

        if opts.rerun_partial and existing:
            conn.execute(
                "DELETE FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
                (arm, session_id, batch_id),
            )
            conn.commit()
        elif existing >= len(rows) and not opts.refresh:
            continue
        elif existing:
            conn.execute(
                "DELETE FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
                (arm, session_id, batch_id),
            )
            conn.commit()

        rendered = "\n\n".join(row["rendered_text"] for row in rows)
        prompt = build_prompt(rendered, rows[0]["item_id"])
        prompt_version = PROMPT_VERSION
        if opts.strict_completeness:
            prompt += M3_STRICT_SUFFIX
            prompt_version = PROMPT_VERSION + "_strict_completeness"

        result = call_minimax(prompt)
        calls_made += 1
        parsed = parse_llm_output(result.response_text or "")
        call_id = f"{arm}:{session_id}:{batch_id}:{datetime.now(timezone.utc).timestamp()}"
        by_id = {row["item_id"]: row for row in rows}
        parsed = [row for row in parsed if row["item_id"] in by_id]
        parsed_ok = len(parsed) == len(rows)

        conn.execute(
            """
            INSERT OR REPLACE INTO model_calls (
              call_id, arm, session_id, batch_id, model_name, prompt_version,
              request_json, response_text, parsed_ok, error, latency_ms,
              input_tokens, output_tokens, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                arm,
                session_id,
                batch_id,
                M3_MODEL,
                prompt_version,
                json.dumps({"prompt": prompt}, ensure_ascii=True),
                result.response_text,
                1 if parsed_ok else 0,
                result.error,
                result.latency_ms,
                result.input_tokens,
                result.output_tokens,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

        ranked = sorted(parsed, key=llm_utility, reverse=True)
        for rank, pred in enumerate(ranked, start=1):
            insert_llm_prediction(
                conn,
                arm,
                session_id,
                batch_id,
                by_id[pred["item_id"]],
                pred,
                rank,
                llm_utility(pred),
                result.response_text or "",
                prompt_version,
                M3_MODEL,
                identity_version=0,
            )
        total_written += len(ranked)
        model_summaries.append(
            {
                "batch_id": batch_id,
                "latency_ms": result.latency_ms,
                "parsed_count": len(parsed),
                "expected_count": len(rows),
                "parsed_ok": parsed_ok,
                "error": result.error,
            }
        )

    return calls_made, total_written, model_summaries


def rank_session_arm(
    conn,
    session_id: str,
    arm_or_mode: str,
    *,
    refresh: bool = True,
    m3_options: M3Options | None = None,
) -> dict:
    """Offline CLI entry: run one arm and return a short status dict."""
    arm = resolve_arm(arm_or_mode)
    rebuild_session_order(conn, session_id)

    if arm == NATIVE_ARM:
        count = _rank_native_arm(conn, session_id, refresh=refresh)
        return {"arm": arm, "predictions": count, "model_calls": []}

    if arm == CHEAP_COMBO_ARM:
        count = _rank_profile_arm(conn, session_id, arm, rank_combo, refresh=refresh)
        return {"arm": arm, "predictions": count, "model_calls": []}

    if arm == BM25_ARM:
        count = _rank_profile_arm(conn, session_id, arm, rank_bm25, refresh=refresh)
        return {"arm": arm, "predictions": count, "model_calls": []}

    if arm == TFIDF_ARM:
        count = _rank_profile_arm(conn, session_id, arm, rank_tfidf, refresh=refresh)
        return {"arm": arm, "predictions": count, "model_calls": []}

    if arm == CHEAP_LINEAR_ARM:
        count = _rank_cheap_linear(conn, session_id, refresh=refresh)
        status = "ok" if count else "skipped"
        reason = None if count else "implementation gated until multiple labeled sessions exist"
        return {"arm": arm, "predictions": count, "status": status, "reason": reason, "model_calls": []}

    if arm == M3_ARM:
        opts = m3_options if m3_options is not None else M3Options(refresh=refresh)
        calls, written, summaries = rank_m3_session(conn, session_id, opts)
        return {
            "arm": arm,
            "predictions": written,
            "calls_made": calls,
            "model_calls": summaries,
        }

    raise ValueError(f"unsupported arm: {arm}")


def ensure_ranked(conn, session_id: str, mode: Mode, refresh: bool = False) -> dict:
    rebuild_session_order(conn, session_id)
    model_calls: list[dict] = []

    if mode == "native":
        _rank_native_arm(conn, session_id, refresh=refresh)
    elif mode == "cheap":
        _rank_profile_arm(conn, session_id, CHEAP_COMBO_ARM, rank_combo, refresh=refresh)
    elif mode == "m3":
        _, _, model_calls = rank_m3_session(
            conn,
            session_id,
            M3Options(refresh=refresh),
        )
        if not existing_count(conn, M3_ARM, session_id):
            _rank_native_arm(conn, session_id, refresh=False)
    else:
        raise ValueError(f"unknown mode: {mode}")

    arm = MODE_TO_ARM[mode]
    effective_arm = arm if existing_count(conn, arm, session_id) else NATIVE_ARM
    rows = conn.execute(
        """
        SELECT p.item_id, p.batch_id, p.rank, p.score, p.pred_stop, p.pred_open,
               p.pred_save, p.pred_look_sec, p.reaction_text, i.rendered_text,
               i.author_handle, i.text, i.original_rank
        FROM arm_predictions p
        JOIN items i ON i.item_id=p.item_id AND i.session_id=p.session_id
        WHERE p.session_id=? AND p.arm=?
        ORDER BY p.batch_id, p.rank
        """,
        (session_id, effective_arm),
    ).fetchall()
    items = [dict(row) for row in rows]
    return {
        "session_id": session_id,
        "mode": mode,
        "arm": arm,
        "effective_arm": effective_arm,
        "ordered_item_ids": [row["item_id"] for row in items],
        "items": items,
        "model_calls": model_calls,
    }
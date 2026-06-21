from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .candidate_window import read_candidate_window_rows
from .db import clear_feed_arm, insert_feed_run
from .parse_llm_outputs import parse_feed_selection_json
from .predictions import insert_llm_prediction
from .profile import load_positive_profile_text
from .prompts import FEED_SELECTION_PROMPT_VERSION, build_feed_selection_prompt
from .rankers.cheap_combo import ARM as CHEAP_COMBO_ARM, rank_combo
from .rankers.features import ml_frontier_lexicon_score, paper_shape_bonus, rank_text, research_url_bonus
from .minimax_client import MiniMaxResult
from .rankers.native import ARM as NATIVE_ARM

DEFAULT_CURATED_K = 12
M3_FEED_ARM = "m3_feed_selection_v0"
M3_PREFILTER_TOP_N = 36
FEED_BATCH_ID = "candidate_window"

ModelCallResult = str | MiniMaxResult
ModelCall = Callable[[str], ModelCallResult]


def _normalize_model_call_result(raw: ModelCallResult) -> tuple[str | None, int, int | None, int | None, str | None]:
    if isinstance(raw, str):
        return raw, 0, None, None, None
    if isinstance(raw, MiniMaxResult):
        return raw.response_text, raw.latency_ms, raw.input_tokens, raw.output_tokens, raw.error
    response_text = getattr(raw, "response_text", None)
    if response_text is not None or hasattr(raw, "latency_ms") or getattr(raw, "error", None):
        return (
            response_text,
            int(getattr(raw, "latency_ms", 0) or 0),
            getattr(raw, "input_tokens", None),
            getattr(raw, "output_tokens", None),
            getattr(raw, "error", None),
        )
    raise TypeError(f"model_call returned unsupported type: {type(raw)!r}")


def _item_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def load_joined_candidate_rows(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Load x_returned_candidates candidate window rows joined with items."""
    window = read_candidate_window_rows(conn, session_id, source=source)
    if not window:
        return []
    ids = [row["item_id"] for row in window]
    placeholders = ",".join("?" for _ in ids)
    item_rows = conn.execute(
        f"""
        SELECT item_id, source, session_id, batch_id, original_rank,
               author_handle, author_name, author_bio, text, quoted_text,
               thread_context, media_desc, link_url, link_title, link_excerpt,
               engagement, rendered_text, raw_json, harvested_at
        FROM items
        WHERE item_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    by_id = {row["item_id"]: _item_row_to_dict(row) for row in item_rows}
    joined: list[dict[str, Any]] = []
    for win in window:
        item = by_id.get(win["item_id"])
        if not item:
            continue
        merged = {**item, **win}
        joined.append(merged)
    return joined


def render_candidate_window_text(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        item_id = row["item_id"]
        text = (row.get("rendered_text") or rank_text(row) or "").strip()
        lines.append(f"id:{item_id} | {text}")
    return "\n".join(lines)


def render_slate_text(rows: list[dict[str, Any]], *, label: str | None = None) -> str:
    blocks: list[str] = []
    for row in rows:
        rendered = (row.get("rendered_text") or "").strip()
        if not rendered:
            rendered = f"<{row['item_id']}>"
        blocks.append(rendered)
    body = "\n\n".join(blocks)
    if label:
        return f"--- {label} ---\n{body}"
    return body


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def feed_run_metrics(
    *,
    candidate_count: int,
    scored_count: int,
    curated_count: int,
    prefilter_count: int | None = None,
) -> dict[str, int | float | None]:
    metrics: dict[str, int | float | None] = {
        "candidate_count": candidate_count,
        "scored_count": scored_count,
        "curated_count": curated_count,
        "coverage_ratio": _ratio(scored_count, candidate_count),
        "curation_ratio": _ratio(curated_count, candidate_count),
        "prefilter_count": prefilter_count,
        "prefilter_ratio": _ratio(prefilter_count, candidate_count) if prefilter_count is not None else None,
    }
    return metrics


def _precision_score(row: dict[str, Any]) -> float:
    text = rank_text(row)
    frontier = max(ml_frontier_lexicon_score(text), paper_shape_bonus(text), research_url_bonus(row))
    code_bonus = 1.0 if "```" in (row.get("text") or "") or "def " in text else 0.0
    return 0.55 * frontier + 0.25 * code_bonus + 0.20 * (1.0 if row.get("link_url") else 0.0)


def _exploration_score(row: dict[str, Any], window_rank: int, candidate_count: int) -> float:
    text = rank_text(row)
    novelty = 1.0 - _ratio(window_rank, max(candidate_count, 1))
    frontier = ml_frontier_lexicon_score(text)
    media = 1.0 if row.get("media_desc") else 0.0
    return 0.40 * novelty + 0.35 * frontier + 0.25 * media


def _balanced_score(row: dict[str, Any], window_rank: int, candidate_count: int) -> float:
    text = rank_text(row)
    position = 1.0 - _ratio(window_rank, max(candidate_count, 1))
    link = 1.0 if row.get("link_url") or row.get("thread_context") else 0.0
    short_penalty = 0.15 if len(text) < 80 else 0.0
    return 0.35 * position + 0.30 * link + 0.35 * (1.0 - short_penalty)


def build_precision_slate(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda r: (_precision_score(r), -int(r.get("window_rank") or 10**9)), reverse=True)
    return ranked[:k]


def build_exploration_slate(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    n = len(rows)
    ranked = sorted(
        rows,
        key=lambda r: (_exploration_score(r, int(r.get("window_rank") or n), n), -int(r.get("window_rank") or 10**9)),
        reverse=True,
    )
    return ranked[:k]


def build_balanced_slate(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    n = len(rows)
    ranked = sorted(
        rows,
        key=lambda r: (_balanced_score(r, int(r.get("window_rank") or n), n), -int(r.get("window_rank") or 10**9)),
        reverse=True,
    )
    return ranked[:k]


def build_native_feed(rows: list[dict[str, Any]], k: int) -> list[str]:
    ordered = sorted(rows, key=lambda r: int(r.get("window_rank") or 10**9))
    return [row["item_id"] for row in ordered[:k]]


def build_cheap_feed(rows: list[dict[str, Any]], k: int, *, positive_profile_text: str | None = None) -> list[str]:
    profile = positive_profile_text if positive_profile_text is not None else load_positive_profile_text()
    ranked = rank_combo(rows, profile)
    return [row["item_id"] for row, _score in ranked[:k]]


def _persist_feed_predictions(
    conn: sqlite3.Connection,
    *,
    arm: str,
    session_id: str,
    rows_by_id: dict[str, dict[str, Any]],
    selected_ids: list[str],
    scored_count: int,
    candidate_count: int,
    prompt_version: str | None = None,
    model_name: str | None = None,
) -> None:
    batch_size = max(1, scored_count)
    for rank, item_id in enumerate(selected_ids, start=1):
        row = rows_by_id[item_id]
        insert_llm_prediction(
            conn,
            arm,
            session_id,
            FEED_BATCH_ID,
            row,
            {
                "pred_stop": 1 if rank <= 20 else 0,
                "pred_open": 1 if rank <= 8 else 0,
                "pred_save": 1 if rank <= 12 else 0,
                "pred_look_sec": max(0.0, 12.0 * (1.0 - rank / batch_size)),
                "reaction_text": "",
            },
            rank,
            float(candidate_count - rank),
            "",
            prompt_version or "",
            model_name or "",
            identity_version=0,
        )


def _finalize_feed_run(
    conn: sqlite3.Connection,
    *,
    arm: str,
    session_id: str,
    candidate_count: int,
    scored_count: int,
    curated_k: int,
    selected_ids: list[str],
    prefilter_count: int | None = None,
    model_call_id: str | None = None,
    prompt_version: str | None = None,
    model_name: str | None = None,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    curated_count = len(selected_ids)
    metrics = feed_run_metrics(
        candidate_count=candidate_count,
        scored_count=scored_count,
        curated_count=curated_count,
        prefilter_count=prefilter_count,
    )
    if extra_metrics:
        metrics.update(extra_metrics)
    feed_run_id = f"{arm}:{session_id}:{datetime.now(timezone.utc).timestamp()}"
    insert_feed_run(
        conn,
        feed_run_id=feed_run_id,
        arm=arm,
        session_id=session_id,
        candidate_count=candidate_count,
        scored_count=scored_count,
        curated_count=curated_count,
        coverage_ratio=float(metrics["coverage_ratio"]),
        curation_ratio=float(metrics["curation_ratio"]),
        curated_k=curated_k,
        metrics=metrics,
        selected_item_ids=selected_ids,
        model_call_id=model_call_id,
        prompt_version=prompt_version,
        model_name=model_name,
        prefilter_count=prefilter_count,
        prefilter_ratio=float(metrics["prefilter_ratio"]) if metrics.get("prefilter_ratio") is not None else None,
    )
    return {
        "feed_run_id": feed_run_id,
        "arm": arm,
        "session_id": session_id,
        "selected_item_ids": selected_ids,
        "metrics": metrics,
    }


def run_native_feed(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    curated_k: int = DEFAULT_CURATED_K,
    refresh: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    if refresh:
        clear_feed_arm(conn, NATIVE_ARM, session_id)
    rows = load_joined_candidate_rows(conn, session_id, source=source)
    candidate_count = len(rows)
    if candidate_count == 0:
        raise ValueError(f"no candidate window rows for session {session_id}")
    selected_ids = build_native_feed(rows, curated_k)
    rows_by_id = {row["item_id"]: row for row in rows}
    _persist_feed_predictions(
        conn,
        arm=NATIVE_ARM,
        session_id=session_id,
        rows_by_id=rows_by_id,
        selected_ids=selected_ids,
        scored_count=candidate_count,
        candidate_count=candidate_count,
    )
    return _finalize_feed_run(
        conn,
        arm=NATIVE_ARM,
        session_id=session_id,
        candidate_count=candidate_count,
        scored_count=candidate_count,
        curated_k=curated_k,
        selected_ids=selected_ids,
    )


def run_cheap_feed(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    curated_k: int = DEFAULT_CURATED_K,
    refresh: bool = False,
    source: str | None = None,
    positive_profile_text: str | None = None,
) -> dict[str, Any]:
    if refresh:
        clear_feed_arm(conn, CHEAP_COMBO_ARM, session_id)
    rows = load_joined_candidate_rows(conn, session_id, source=source)
    candidate_count = len(rows)
    if candidate_count == 0:
        raise ValueError(f"no candidate window rows for session {session_id}")
    selected_ids = build_cheap_feed(rows, curated_k, positive_profile_text=positive_profile_text)
    rows_by_id = {row["item_id"]: row for row in rows}
    _persist_feed_predictions(
        conn,
        arm=CHEAP_COMBO_ARM,
        session_id=session_id,
        rows_by_id=rows_by_id,
        selected_ids=selected_ids,
        scored_count=candidate_count,
        candidate_count=candidate_count,
    )
    return _finalize_feed_run(
        conn,
        arm=CHEAP_COMBO_ARM,
        session_id=session_id,
        candidate_count=candidate_count,
        scored_count=candidate_count,
        curated_k=curated_k,
        selected_ids=selected_ids,
    )



def _m3_feed_selection_parse_repair_suffix(validation_error: str, candidate_ids: list[str]) -> str:
    allowed = ", ".join(candidate_ids)
    return (
        "\n\n---\n"
        "Your previous output failed JSON validation.\n"
        f"Validation error: {validation_error}\n"
        "Reply with strict JSON only (no markdown fences).\n"
        "Every id in selected_item_ids must appear exactly as a key in predicted_behavior.\n"
        f"Allowed candidate item ids from this window (use these exact strings): {allowed}\n"
        "Do not invent ids. Keys in predicted_behavior must match selected_item_ids exactly.\n"
    )


def run_m3_feed_selection(
    conn: sqlite3.Connection,
    session_id: str,
    model_call: ModelCall,
    *,
    curated_k: int = DEFAULT_CURATED_K,
    refresh: bool = False,
    source: str | None = None,
    identity_revealed: str = "",
    identity_endorsed: str = "",
    state_preamble: str = "ordinary scroll session. a few minutes to look around.",
    model_name: str | None = None,
    prefilter_top_n: int = M3_PREFILTER_TOP_N,
    max_attempts: int = 2,
) -> dict[str, Any]:
    if refresh:
        clear_feed_arm(conn, M3_FEED_ARM, session_id)
    rows = load_joined_candidate_rows(conn, session_id, source=source)
    candidate_count = len(rows)
    if candidate_count == 0:
        raise ValueError(f"no candidate window rows for session {session_id}")

    profile = load_positive_profile_text()
    prefilter_k = min(prefilter_top_n, candidate_count)
    ranked = rank_combo(rows, profile)
    cheap_ranked = [row["item_id"] for row, _score in ranked[:prefilter_k]]
    prefilter_rows = [row for row in rows if row["item_id"] in set(cheap_ranked)]
    prefilter_rows.sort(key=lambda r: cheap_ranked.index(r["item_id"]))
    prefilter_count = len(prefilter_rows)

    precision = build_precision_slate(prefilter_rows, curated_k)
    exploration = build_exploration_slate(prefilter_rows, curated_k)
    balanced = build_balanced_slate(prefilter_rows, curated_k)

    candidate_ids = [row["item_id"] for row in prefilter_rows]
    prompt = build_feed_selection_prompt(
        render_candidate_window_text(prefilter_rows),
        render_slate_text(precision),
        render_slate_text(exploration),
        render_slate_text(balanced),
        identity_revealed=identity_revealed,
        identity_endorsed=identity_endorsed,
        state_preamble=state_preamble,
        curation_target=f"choose {curated_k} items",
    )
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    parsed: dict[str, Any] | None = None
    call_id: str | None = None
    response_text: str | None = None
    latency_ms = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    current_prompt = prompt
    last_parse_error: ValueError | None = None

    for attempt in range(max_attempts):
        call_id = f"{M3_FEED_ARM}:{session_id}:{datetime.now(timezone.utc).timestamp()}:{attempt}"
        raw = model_call(current_prompt)
        response_text, latency_ms, input_tokens, output_tokens, model_error = _normalize_model_call_result(raw)

        if model_error:
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
                    M3_FEED_ARM,
                    session_id,
                    FEED_BATCH_ID,
                    model_name,
                    FEED_SELECTION_PROMPT_VERSION,
                    json.dumps({"prompt": current_prompt}, ensure_ascii=True),
                    response_text,
                    0,
                    model_error,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            raise ValueError(f"MiniMax model call failed: {model_error}")

        parsed_ok = 1
        error: str | None = None
        try:
            parsed = parse_feed_selection_json(response_text or "", candidate_ids, target_n=curated_k)
        except ValueError as exc:
            parsed_ok = 0
            error = str(exc)
            last_parse_error = exc
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
                    M3_FEED_ARM,
                    session_id,
                    FEED_BATCH_ID,
                    model_name,
                    FEED_SELECTION_PROMPT_VERSION,
                    json.dumps({"prompt": current_prompt}, ensure_ascii=True),
                    response_text,
                    parsed_ok,
                    error,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            if attempt + 1 < max_attempts:
                current_prompt = current_prompt + _m3_feed_selection_parse_repair_suffix(error, candidate_ids)
                continue
            raise last_parse_error from None

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
                M3_FEED_ARM,
                session_id,
                FEED_BATCH_ID,
                model_name,
                FEED_SELECTION_PROMPT_VERSION,
                json.dumps({"prompt": current_prompt}, ensure_ascii=True),
                response_text,
                parsed_ok,
                error,
                latency_ms,
                input_tokens,
                output_tokens,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        break

    if parsed is None or call_id is None:
        raise RuntimeError("m3 feed selection finished without a parsed result")

    rows_by_id = {row["item_id"]: row for row in rows}
    selected_ids = parsed["selected_item_ids"]
    for rank, item_id in enumerate(selected_ids, start=1):
        behavior = parsed["predicted_behavior"][item_id]
        insert_llm_prediction(
            conn,
            M3_FEED_ARM,
            session_id,
            FEED_BATCH_ID,
            rows_by_id[item_id],
            {
                "pred_stop": 1 if behavior["stop"] else 0,
                "pred_open": 1 if behavior["open"] else 0,
                "pred_save": 1 if behavior["save"] else 0,
                "pred_look_sec": behavior["look_sec"],
                "reaction_text": behavior.get("why") or "",
            },
            rank,
            float(parsed["feed_scores"].get(parsed["selected_feed"], 0.0)),
            response_text,
            FEED_SELECTION_PROMPT_VERSION,
            model_name or "",
            identity_version=0,
        )

    return _finalize_feed_run(
        conn,
        arm=M3_FEED_ARM,
        session_id=session_id,
        candidate_count=candidate_count,
        scored_count=prefilter_count,
        curated_k=curated_k,
        selected_ids=selected_ids,
        prefilter_count=prefilter_count,
        model_call_id=call_id,
        prompt_version=FEED_SELECTION_PROMPT_VERSION,
        model_name=model_name,
        extra_metrics={
            "selected_feed": parsed["selected_feed"],
            "feed_scores": parsed["feed_scores"],
            "dropped_item_ids": parsed["dropped_item_ids"],
            "short_verdict": parsed["short_verdict"],
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
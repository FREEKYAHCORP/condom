from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from condom_core.config import PROFILE
from condom_core.db import clear_arm
from condom_core.ingest import rebuild_session_order
from condom_core.minimax_client import call_minimax
from condom_core.parse_llm_outputs import parse_llm_output
from condom_core.predictions import batches_for_session, insert_llm_prediction, insert_ranked_predictions
from condom_core.prompts import PROMPT_VERSION, build_prompt
from condom_core.rankers.cheap_combo import rank_combo
from condom_core.rankers.native import rank as rank_native

Mode = Literal["native", "cheap", "m3"]

MODE_TO_ARM = {
    "native": "native_x_order",
    "cheap": "cheap_combo_v0",
    "m3": "llm_usersim_encounter",
}
MODEL = "MiniMax-M3"


def _profile_text() -> str:
    path = PROFILE / "positive_profile.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "machine learning\nagents\nAI infrastructure\nbenchmarks\nevaluations\nopen source\n"


def _llm_utility(row: dict) -> float:
    return (
        3.0 * row["pred_save"]
        + 1.5 * row["pred_open"]
        + 0.5 * row["pred_stop"]
        + 0.05 * min(row["pred_look_sec"], 20)
    )


def _existing_count(conn, arm: str, session_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM arm_predictions WHERE arm=? AND session_id=?",
        (arm, session_id),
    ).fetchone()["n"]


def _rank_native(conn, session_id: str, refresh: bool) -> None:
    arm = MODE_TO_ARM["native"]
    if refresh or not _existing_count(conn, arm, session_id):
        clear_arm(conn, arm, session_id)
        for batch_id, rows in batches_for_session(conn, session_id).items():
            insert_ranked_predictions(conn, arm, session_id, batch_id, rank_native(rows))


def _rank_cheap(conn, session_id: str, refresh: bool) -> None:
    arm = MODE_TO_ARM["cheap"]
    if refresh or not _existing_count(conn, arm, session_id):
        clear_arm(conn, arm, session_id)
        profile = _profile_text()
        for batch_id, rows in batches_for_session(conn, session_id).items():
            insert_ranked_predictions(conn, arm, session_id, batch_id, rank_combo(rows, profile))


def _rank_m3(conn, session_id: str, refresh: bool) -> list[dict]:
    arm = MODE_TO_ARM["m3"]
    model_summaries = []
    if refresh:
        clear_arm(conn, arm, session_id)
    for batch_id, rows in batches_for_session(conn, session_id).items():
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
            (arm, session_id, batch_id),
        ).fetchone()["n"]
        if existing >= len(rows) and not refresh:
            continue
        conn.execute(
            "DELETE FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
            (arm, session_id, batch_id),
        )
        conn.commit()
        if not rows:
            continue
        rendered = "\n\n".join(row["rendered_text"] for row in rows)
        prompt = build_prompt(rendered, rows[0]["item_id"])
        result = call_minimax(prompt)
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
                MODEL,
                PROMPT_VERSION,
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
        ranked = sorted(parsed, key=_llm_utility, reverse=True)
        for rank, pred in enumerate(ranked, start=1):
            insert_llm_prediction(
                conn,
                arm,
                session_id,
                batch_id,
                by_id[pred["item_id"]],
                pred,
                rank,
                _llm_utility(pred),
                result.response_text or "",
                PROMPT_VERSION,
                MODEL,
                identity_version=0,
            )
        model_summaries.append({
            "batch_id": batch_id,
            "latency_ms": result.latency_ms,
            "parsed_count": len(parsed),
            "expected_count": len(rows),
            "parsed_ok": parsed_ok,
            "error": result.error,
        })
    return model_summaries


def ensure_ranked(conn, session_id: str, mode: Mode, refresh: bool = False) -> dict:
    rebuild_session_order(conn, session_id)
    if mode == "native":
        _rank_native(conn, session_id, refresh)
        model_calls = []
    elif mode == "cheap":
        _rank_cheap(conn, session_id, refresh)
        model_calls = []
    elif mode == "m3":
        model_calls = _rank_m3(conn, session_id, refresh)
        # If M3 failed before any prediction, make sure native is available as a UI fallback.
        if not _existing_count(conn, MODE_TO_ARM["m3"], session_id):
            _rank_native(conn, session_id, False)
    else:
        raise ValueError(f"unknown mode: {mode}")
    arm = MODE_TO_ARM[mode]
    effective_arm = arm if _existing_count(conn, arm, session_id) else MODE_TO_ARM["native"]
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

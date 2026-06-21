from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import clear_arm, connect
from condom_core.minimax_client import call_minimax
from condom_core.parse_llm_outputs import parse_llm_output
from condom_core.predictions import batches_for_session, insert_llm_prediction
from condom_core.prompts import PROMPT_VERSION, build_prompt


ARM = "llm_usersim_encounter"
MODEL = "MiniMax-M3"
STRICT_SUFFIX = """

IMPORTANT COMPLETENESS CHECK:
The timeline above contains multiple item ids. Return one block for every id in
the timeline, in the same order. Do not stop after the first item.
"""


def utility(row: dict) -> float:
    return (
        3.0 * row["pred_save"]
        + 1.5 * row["pred_open"]
        + 0.5 * row["pred_stop"]
        + 0.05 * min(row["pred_look_sec"], 20)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--limit-batches", type=int)
    parser.add_argument("--skip-complete", action="store_true")
    parser.add_argument("--rerun-partial", action="store_true")
    parser.add_argument("--strict-completeness", action="store_true")
    parser.add_argument("--max-calls", type=int)
    args = parser.parse_args()

    conn = connect()
    if not (args.skip_complete or args.rerun_partial):
        clear_arm(conn, ARM, args.session_id)
    batches = list(batches_for_session(conn, args.session_id).items())
    if args.limit_batches:
        batches = batches[: args.limit_batches]
    total = 0
    calls_made = 0
    for batch_id, rows in batches:
        if args.max_calls is not None and calls_made >= args.max_calls:
            break
        existing_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM arm_predictions
            WHERE arm=? AND session_id=? AND batch_id=?
            """,
            (ARM, args.session_id, batch_id),
        ).fetchone()["n"]
        if args.skip_complete and existing_count >= len(rows):
            continue
        if args.rerun_partial and existing_count >= len(rows):
            continue
        if args.rerun_partial and existing_count:
            conn.execute(
                "DELETE FROM arm_predictions WHERE arm=? AND session_id=? AND batch_id=?",
                (ARM, args.session_id, batch_id),
            )
            conn.commit()
        rendered = "\n\n".join(row["rendered_text"] for row in rows)
        prompt = build_prompt(rendered, rows[0]["item_id"])
        prompt_version = PROMPT_VERSION
        if args.strict_completeness:
            prompt += STRICT_SUFFIX
            prompt_version = PROMPT_VERSION + "_strict_completeness"
        result = call_minimax(prompt)
        calls_made += 1
        call_id = f"{ARM}:{args.session_id}:{batch_id}"
        parsed = parse_llm_output(result.response_text or "")
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
                ARM,
                args.session_id,
                batch_id,
                MODEL,
                prompt_version,
                json.dumps({"prompt": prompt}, ensure_ascii=True),
                result.response_text,
                1 if parsed else 0,
                result.error,
                result.latency_ms,
                result.input_tokens,
                result.output_tokens,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        by_id = {row["item_id"]: row for row in rows}
        parsed = [row for row in parsed if row["item_id"] in by_id]
        ranked = sorted(parsed, key=utility, reverse=True)
        for rank, pred in enumerate(ranked, start=1):
            score = utility(pred)
            insert_llm_prediction(
                conn,
                ARM,
                args.session_id,
                batch_id,
                by_id[pred["item_id"]],
                pred,
                rank,
                score,
                result.response_text or "",
                prompt_version,
                MODEL,
            )
        total += len(ranked)
        print(f"{ARM}: batch {batch_id} parsed {len(ranked)}/{len(rows)}")
    print(f"{ARM}: made {calls_made} calls, wrote {total} predictions")


if __name__ == "__main__":
    main()

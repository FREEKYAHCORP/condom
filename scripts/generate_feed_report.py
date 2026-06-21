from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.candidate_window import backfill_candidate_window_from_raw
from condom_core.config import RUNS
from condom_core.db import connect, init_db
from condom_core.feed_generation import (
    run_cheap_feed,
    run_m3_feed_selection,
    run_native_feed,
)
from condom_core.feed_judge import (
    DEFAULT_JUDGE_ARMS,
    build_blind_judge_packet,
    insert_feed_evaluation,
)
from condom_core.minimax_client import call_minimax


def _resolve_run_dir(session_id: str, run_dir: str | None) -> Path:
    if run_dir:
        return Path(run_dir)
    return RUNS / session_id / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _m3_model_call(prompt: str):
    return call_minimax(prompt)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline pipeline: backfill window, generate feeds, blind judge packet, HTML report.",
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--db-path", default=None, help="SQLite path (default: data/processed/experiment.sqlite)")
    parser.add_argument("--curated-k", type=int, default=12)
    parser.add_argument("--prefilter-top-n", type=int, default=36)
    parser.add_argument("--skip-m3", action="store_true", help="Skip M3 feed generation and blind judge packet")
    parser.add_argument("--judge-seed", type=int, default=7)
    parser.add_argument("--run-dir", default=None, help="Output directory for report HTML and judge artifacts")
    parser.add_argument("--persist-evaluation", action="store_true", help="Store feed_evaluations row when judge packet is built")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    conn = connect(db_path) if db_path else connect()
    init_db(conn)

    summary: dict[str, object] = {"session_id": args.session_id}

    backfill = backfill_candidate_window_from_raw(conn, args.session_id)
    summary["backfill"] = backfill

    summary["native_feed"] = run_native_feed(
        conn,
        args.session_id,
        curated_k=args.curated_k,
        refresh=True,
    )
    summary["cheap_feed"] = run_cheap_feed(
        conn,
        args.session_id,
        curated_k=args.curated_k,
        refresh=True,
    )

    if args.skip_m3:
        summary["m3_feed"] = None
        summary["judge_packet"] = None
        summary["judge_skipped_reason"] = "skip_m3"
    else:
        summary["m3_feed"] = run_m3_feed_selection(
            conn,
            args.session_id,
            _m3_model_call,
            curated_k=args.curated_k,
            prefilter_top_n=args.prefilter_top_n,
            refresh=True,
            model_name="MiniMax-M3",
        )
        judge_dir = _resolve_run_dir(args.session_id, args.run_dir) / "judge_packet"
        try:
            packet = build_blind_judge_packet(
                conn,
                session_id=args.session_id,
                seed=args.judge_seed,
                output_dir=judge_dir,
                arms=DEFAULT_JUDGE_ARMS,
            )
            summary["judge_packet"] = {
                "packet_path": str(packet["packet_path"]),
                "key_path": str(packet["key_path"]),
                "meta_path": str(packet["meta_path"]),
                "leakage": packet["leakage"],
            }
            if args.persist_evaluation:
                evaluation_id = f"{args.session_id}:qual_judge:{args.judge_seed}"
                insert_feed_evaluation(
                    conn,
                    evaluation_id=evaluation_id,
                    session_id=args.session_id,
                    packet_path=str(packet["packet_path"]),
                    key_path=str(packet["key_path"]),
                    meta_path=str(packet["meta_path"]),
                    prompt_version="qualitative_feed_judge_v0.md",
                    label_map_json=json.dumps(packet["label_to_arm"]),
                    leakage_ok=bool(packet["leakage"]["ok"]),
                    leakage_json=json.dumps(packet["leakage"]),
                )
                summary["evaluation_id"] = evaluation_id
        except ValueError as exc:
            summary["judge_packet"] = None
            summary["judge_skipped_reason"] = str(exc)

    run_dir = _resolve_run_dir(args.session_id, args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    render_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "render_feeds.py"),
        "--session-id",
        args.session_id,
        "--run-dir",
        str(run_dir),
    ]
    if args.db_path:
        render_cmd.extend(["--db-path", str(db_path)])
    proc = subprocess.run(render_cmd, capture_output=True, text=True, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError(
            f"render_feeds failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
        )
    render_out = json.loads(proc.stdout)
    summary["run_dir"] = str(run_dir)
    summary["report"] = render_out

    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
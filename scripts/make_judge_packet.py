from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect, init_db
from condom_core.feed_judge import build_blind_judge_packet, insert_feed_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Build blind qualitative judge packet from latest feed_runs.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--limit", type=int, default=None, help="Max items per feed (default: all curated items)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--persist", action="store_true", help="Store feed_evaluations row in SQLite")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    result = build_blind_judge_packet(
        conn,
        session_id=args.session_id,
        limit=args.limit,
        seed=args.seed,
    )

    if args.persist:
        evaluation_id = f"{args.session_id}:qual_judge:{args.seed}"
        insert_feed_evaluation(
            conn,
            evaluation_id=evaluation_id,
            session_id=args.session_id,
            packet_path=str(result["packet_path"]),
            key_path=str(result["key_path"]),
            meta_path=str(result["meta_path"]),
            prompt_version="qualitative_feed_judge_v0.md",
            label_map_json=json.dumps(result["label_to_arm"]),
            leakage_ok=bool(result["leakage"]["ok"]),
            leakage_json=json.dumps(result["leakage"]),
        )

    print(json.dumps({
        "packet_path": str(result["packet_path"]),
        "key_path": str(result["key_path"]),
        "meta_path": str(result["meta_path"]),
        "leakage": result["leakage"],
    }, indent=2))


if __name__ == "__main__":
    main()
from __future__ import annotations

import argparse
import random
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import PROMPTS, RUNS
from condom_core.db import connect

ARMS = ["native_x_order", "cheap_combo_v0", "llm_usersim_encounter"]


def feed_text(conn, session_id: str, arm: str, limit: int) -> str:
    rows = conn.execute(
        """
        SELECT p.rank, i.original_rank, i.author_handle, i.text, p.reaction_text
        FROM arm_predictions p
        JOIN items i ON i.item_id=p.item_id AND i.session_id=p.session_id
        WHERE p.session_id=? AND p.arm=?
        ORDER BY p.batch_id, p.rank
        LIMIT ?
        """,
        (session_id, arm, limit),
    ).fetchall()
    parts = []
    for idx, row in enumerate(rows, start=1):
        text = " ".join((row["text"] or "").split())
        parts.append(f"{idx}. @{row['author_handle'] or 'unknown'} · native_rank={row['original_rank']}\n{text}")
    return "\n\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    conn = connect()
    labels = ["A", "B", "C"]
    arms = list(ARMS)
    rng = random.Random(args.seed)
    rng.shuffle(arms)
    mapping = dict(zip(labels, arms))
    template = (PROMPTS / "qualitative_feed_judge_v0.md").read_text(encoding="utf-8")
    prompt = template
    for label in labels:
        prompt = prompt.replace(f"{{{{FEED_{label}}}}}", feed_text(conn, args.session_id, mapping[label], args.limit))
    run_dir = RUNS / "judge_packets"
    run_dir.mkdir(parents=True, exist_ok=True)
    packet_path = run_dir / f"{args.session_id}_qual_judge.md"
    key_path = run_dir / f"{args.session_id}_qual_judge_key.txt"
    packet_path.write_text(prompt, encoding="utf-8")
    key_path.write_text("\n".join(f"Feed {label}: {arm}" for label, arm in mapping.items()) + "\n", encoding="utf-8")
    print(packet_path)
    print(key_path)


if __name__ == "__main__":
    main()

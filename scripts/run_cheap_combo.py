import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import PROFILE
from condom_core.db import clear_arm, connect
from condom_core.predictions import batches_for_session, insert_ranked_predictions
from condom_core.rankers.cheap_combo import ARM, rank_combo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    profile = (PROFILE / "positive_profile.txt").read_text(encoding="utf-8")
    conn = connect()
    clear_arm(conn, ARM, args.session_id)
    count = 0
    for batch_id, rows in batches_for_session(conn, args.session_id).items():
        insert_ranked_predictions(conn, ARM, args.session_id, batch_id, rank_combo(rows, profile))
        count += len(rows)
    print(f"{ARM}: wrote {count} predictions")


if __name__ == "__main__":
    main()

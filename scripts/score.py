import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect
from condom_core.scoring.compare import score_arm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    conn = connect()
    arms = [row["arm"] for row in conn.execute(
        "SELECT DISTINCT arm FROM arm_predictions WHERE session_id=? ORDER BY arm",
        (args.session_id,),
    )]
    for arm in arms:
        result = score_arm(conn, arm, args.session_id)
        if not result.get("headline_valid"):
            print(f"{arm}: n={result['n_exposed']} saves={result['n_saves']} save_fbeta=INVALID(no saves)")
        else:
            print(f"{arm}: n={result['n_exposed']} saves={result['n_saves']} fbeta={result['save_fbeta']:.4f}")


if __name__ == "__main__":
    main()

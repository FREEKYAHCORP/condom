import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import RUNS
from condom_core.db import connect
from condom_core.scoring.compare import dump_results, markdown_table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    conn = connect()
    rows = conn.execute(
        """
        SELECT *
        FROM scores
        WHERE session_id=?
        ORDER BY save_fbeta DESC, false_skip_saves ASC, false_pull_count ASC
        """,
        (args.session_id,),
    ).fetchall()
    results = [dict(row) for row in rows]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    dump_results(run_dir, results)
    print(markdown_table(results))
    print(f"wrote {run_dir}")


if __name__ == "__main__":
    main()

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect, init_db


def main() -> None:
    conn = connect()
    init_db(conn)
    print("initialized data/processed/experiment.sqlite")


if __name__ == "__main__":
    main()

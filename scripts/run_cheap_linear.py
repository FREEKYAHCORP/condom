import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import clear_arm, connect
from condom_core.rankers.cheap_linear import ARM, can_train


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    conn = connect()
    clear_arm(conn, ARM, args.session_id)
    if not can_train(conn, args.session_id):
        print(f"{ARM}: skipped, insufficient prior labeled sessions")
        return
    print(f"{ARM}: skipped, implementation gated until multiple labeled sessions exist")


if __name__ == "__main__":
    main()

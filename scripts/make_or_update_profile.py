from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect
from condom_core.profile import write_profile_from_saved_items


def main() -> None:
    conn = connect()
    print(write_profile_from_saved_items(conn))


if __name__ == "__main__":
    main()
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import PROFILE, ensure_dirs
from condom_core.db import connect


def main() -> None:
    ensure_dirs()
    conn = connect()
    rows = conn.execute(
        """
        SELECT DISTINCT i.rendered_text
        FROM items i
        JOIN events e ON e.item_id = i.item_id AND e.session_id = i.session_id
        WHERE e.save = 1
        ORDER BY i.harvested_at
        """
    ).fetchall()
    path = PROFILE / "positive_profile.txt"
    if rows:
        path.write_text("\n\n".join(row["rendered_text"] for row in rows), encoding="utf-8")
        print(f"wrote profile from {len(rows)} saved items")
    elif not path.exists():
        path.write_text("machine learning\nAI agents\nsecurity research\ndeveloper tools\n", encoding="utf-8")
        print("wrote manual fallback profile")
    else:
        print("kept existing manual fallback profile")


if __name__ == "__main__":
    main()

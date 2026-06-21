from __future__ import annotations

from condom_core.config import PROFILE, ensure_dirs
from condom_core.db import connect

DEFAULT_POSITIVE_PROFILE = (
    "machine learning\n"
    "agents\n"
    "AI infrastructure\n"
    "benchmarks\n"
    "evaluations\n"
    "open source\n"
)


def positive_profile_path() -> str:
    return str(PROFILE / "positive_profile.txt")


def load_positive_profile_text() -> str:
    path = PROFILE / "positive_profile.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return DEFAULT_POSITIVE_PROFILE


def write_profile_from_saved_items(conn=None) -> str:
    """Rebuild positive_profile.txt from saved items, or ensure default fallback exists."""
    ensure_dirs()
    if conn is None:
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
        return f"wrote profile from {len(rows)} saved items"
    if not path.exists():
        path.write_text(DEFAULT_POSITIVE_PROFILE, encoding="utf-8")
        return "wrote default fallback profile"
    return "kept existing profile"
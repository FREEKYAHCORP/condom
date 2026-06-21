from __future__ import annotations


ARM = "cheap_linear_v1"


def can_train(conn, session_id: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT session_id) AS n_sessions, SUM(save) AS n_saves
        FROM events
        WHERE exposed=1 AND session_id != ?
        """,
        (session_id,),
    ).fetchone()
    return bool(row and row["n_sessions"] and (row["n_saves"] or 0) > 0)

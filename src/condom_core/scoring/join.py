from __future__ import annotations


def joined_rows(conn, arm: str, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          ? AS arm, i.item_id, i.session_id, i.batch_id, p.rank, p.score,
          COALESCE(p.pred_stop, 0) AS pred_stop,
          COALESCE(p.pred_open, 0) AS pred_open,
          COALESCE(p.pred_save, 0) AS pred_save,
          COALESCE(p.pred_look_sec, 0) AS pred_look_sec,
          COALESCE(MAX(e.save), 0) AS obs_save,
          COALESCE(MAX(e.stop), 0) AS obs_stop,
          COALESCE(MAX(e.thread_open), 0) AS thread_open,
          COALESCE(MAX(e.profile_open), 0) AS profile_open,
          COALESCE(MAX(e.link_click), 0) AS link_click,
          COALESCE(MAX(e.look_sec), 0) AS obs_look
        FROM items i
        JOIN events e
          ON e.item_id = i.item_id
         AND e.session_id = i.session_id
         AND e.exposed = 1
        LEFT JOIN arm_predictions p
          ON p.item_id = i.item_id
         AND p.session_id = i.session_id
         AND p.arm = ?
        WHERE i.session_id = ?
          AND EXISTS (
            SELECT 1 FROM arm_predictions ap
            WHERE ap.arm = ? AND ap.session_id = ?
          )
        GROUP BY i.item_id
        """,
        (arm, arm, session_id, arm, session_id),
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["obs_open"] = 1 if d["thread_open"] or d["profile_open"] or d["link_click"] else 0
        d["obs_utility"] = (
            3.0 * d["obs_save"]
            + 1.5 * d["thread_open"]
            + 1.0 * d["profile_open"]
            + 1.0 * d["link_click"]
            + 0.5 * d["obs_stop"]
            + 0.05 * min(d["obs_look"] or 0, 20)
        )
        out.append(d)
    return out

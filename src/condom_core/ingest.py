from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .db import insert_events, insert_raw_network_response, upsert_items
from .parse_x import item_from_dom, parse_response


def normalize_event(ev: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    item_id = str(ev.get("item_id") or "")
    if not item_id:
        return None
    out = dict(ev)
    out.setdefault("event_id", f"{session_id}:{item_id}:{out.get('ts') or ''}")
    out.setdefault("session_id", session_id)
    out["item_id"] = item_id
    out["exposed"] = int(out.get("exposed", 1))
    out.setdefault("visible_ms", 0)
    out.setdefault("stop", 0)
    out.setdefault("save", 0)
    out.setdefault("look_sec", (out.get("visible_ms") or 0) / 1000)
    out.setdefault("profile_open", 0)
    out.setdefault("thread_open", 0)
    out.setdefault("link_click", 0)
    out.setdefault("lens_feedback", None)
    out.setdefault("exposed_surface", "x_for_you")
    out.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return out


def ingest_dom_items(conn, session_id: str, rows: list[dict[str, Any]]) -> int:
    items = [item for item in (item_from_dom(row, session_id) for row in rows) if item]
    return upsert_items(conn, items, datetime.now(timezone.utc).isoformat())


def ingest_events(conn, session_id: str, rows: list[dict[str, Any]]) -> int:
    normalized = [ev for ev in (normalize_event(row, session_id) for row in rows) if ev]
    count = insert_events(conn, normalized)
    rebuild_session_order(conn, session_id)
    return count


def ingest_raw_response(
    conn,
    *,
    session_id: str,
    response_id: str,
    url: str,
    body: str | dict[str, Any] | list[Any],
    captured_at: str | None = None,
) -> dict[str, Any]:
    body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=True)
    parsed_ok = False
    parsed_count = 0
    error = None
    items = []
    try:
        parsed_body = json.loads(body_text) if isinstance(body_text, str) else body_text
        items = parse_response(parsed_body, session_id=session_id)
        parsed_ok = True
        parsed_count = len(items)
    except Exception as exc:  # malformed response bodies are data, not crashes
        error = str(exc)
    insert_raw_network_response(
        conn,
        response_id=response_id,
        session_id=session_id,
        url=url,
        body=body_text,
        parsed_ok=parsed_ok,
        parsed_count=parsed_count,
        error=error,
        captured_at=captured_at,
    )
    if items:
        upsert_items(conn, items, datetime.now(timezone.utc).isoformat())
        rebuild_session_order(conn, session_id)
    return {"accepted": True, "parsed_ok": parsed_ok, "parsed_count": parsed_count, "error": error}


def rebuild_session_order(conn, session_id: str, batch_size: int = 40) -> int:
    """Assign original_rank/batch_id from first exposed event order.

    This mirrors scripts/10_ingest_raw.py: exposure order is the native X order
    spine. Items without an exposed event remain unranked and are excluded from
    scoring/ranking until exposure is known.
    """
    rows = conn.execute(
        """
        SELECT item_id, MIN(ts) AS first_ts
        FROM events
        WHERE session_id=? AND exposed=1
        GROUP BY item_id
        ORDER BY first_ts, item_id
        """,
        (session_id,),
    ).fetchall()
    updated = 0
    for idx, row in enumerate(rows, start=1):
        batch_id = f"{session_id}_b{(idx - 1) // batch_size:03d}"
        cur = conn.execute(
            """
            UPDATE items
            SET original_rank=?, batch_id=?
            WHERE session_id=? AND item_id=?
            """,
            (idx, batch_id, session_id, row["item_id"]),
        )
        updated += cur.rowcount
    conn.commit()
    return updated

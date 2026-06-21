from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import Item
from .parse_x import parse_response_ordered


def insert_x_returned_candidates(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    response_id: str,
    ordered: list[tuple[Item, int]],
    source: str,
    captured_at: str,
) -> int:
    """Persist per-response candidate order into x_returned_candidates."""
    conn.execute(
        "DELETE FROM x_returned_candidates WHERE response_id = ?",
        (response_id,),
    )
    count = 0
    for item, response_rank in ordered:
        conn.execute(
            """
            INSERT INTO x_returned_candidates (
              response_id, session_id, item_id, response_rank, source, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                response_id,
                session_id,
                item.item_id,
                response_rank,
                source,
                captured_at,
            ),
        )
        count += 1
    conn.commit()
    return count


def rebuild_candidate_window(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    source: str = "x_for_you",
) -> int:
    """Merge x_returned_candidates into one session window (first occurrence wins)."""
    rows = conn.execute(
        """
        SELECT
          xrc.item_id,
          xrc.response_id,
          xrc.captured_at,
          xrc.source
        FROM x_returned_candidates AS xrc
        JOIN raw_network_responses AS rnr ON rnr.response_id = xrc.response_id
        WHERE xrc.session_id = ?
        ORDER BY rnr.captured_at ASC, rnr.response_id ASC, xrc.response_rank ASC
        """,
        (session_id,),
    ).fetchall()

    seen: set[str] = set()
    merged: list[tuple[str, str, str, str]] = []
    for row in rows:
        item_id = row["item_id"]
        if item_id in seen:
            continue
        seen.add(item_id)
        merged.append(
            (
                item_id,
                row["response_id"],
                row["captured_at"],
                row["source"] or source,
            )
        )

    rebuilt_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "DELETE FROM candidate_window_items WHERE session_id = ?",
        (session_id,),
    )
    window_rank = 0
    for item_id, response_id, captured_at, row_source in merged:
        window_rank += 1
        conn.execute(
            """
            INSERT INTO candidate_window_items (
              session_id, item_id, window_rank, first_response_id,
              first_captured_at, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                item_id,
                window_rank,
                response_id,
                captured_at,
                row_source,
            ),
        )

    if merged:
        conn.execute(
            """
            INSERT INTO candidate_windows (session_id, source, rebuilt_at, item_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              source = excluded.source,
              rebuilt_at = excluded.rebuilt_at,
              item_count = excluded.item_count
            """,
            (session_id, source, rebuilt_at, window_rank),
        )
    else:
        conn.execute(
            "DELETE FROM candidate_windows WHERE session_id = ?",
            (session_id,),
        )
    conn.commit()
    return window_rank


def backfill_x_returned_candidates_from_raw(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    default_source: str = "x_for_you",
) -> int:
    """Parse stored raw_network_responses and populate x_returned_candidates."""
    responses = conn.execute(
        """
        SELECT response_id, body, captured_at
        FROM raw_network_responses
        WHERE session_id = ? AND parsed_ok = 1
        ORDER BY captured_at ASC, response_id ASC
        """,
        (session_id,),
    ).fetchall()

    processed = 0
    for row in responses:
        try:
            body = json.loads(row["body"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(body, dict):
            continue
        ordered = parse_response_ordered(
            body,
            session_id=session_id,
            source=default_source,
        )
        if not ordered:
            continue
        insert_x_returned_candidates(
            conn,
            session_id=session_id,
            response_id=row["response_id"],
            ordered=ordered,
            source=default_source,
            captured_at=row["captured_at"],
        )
        processed += 1
    return processed


def backfill_candidate_window_from_raw(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    default_source: str = "x_for_you",
) -> dict[str, int]:
    """Backfill per-response candidates from raw bodies, then rebuild the window."""
    responses = backfill_x_returned_candidates_from_raw(
        conn,
        session_id,
        default_source=default_source,
    )
    items = rebuild_candidate_window(conn, session_id, source=default_source)
    return {"responses_processed": responses, "window_item_count": items}


def read_candidate_window_rows(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Return candidate window rows ordered by window_rank."""
    if source is None:
        rows = conn.execute(
            """
            SELECT session_id, item_id, window_rank, first_response_id,
                   first_captured_at, source
            FROM candidate_window_items
            WHERE session_id = ?
            ORDER BY window_rank ASC
            """,
            (session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT session_id, item_id, window_rank, first_response_id,
                   first_captured_at, source
            FROM candidate_window_items
            WHERE session_id = ? AND source = ?
            ORDER BY window_rank ASC
            """,
            (session_id, source),
        ).fetchall()
    return [dict(row) for row in rows]
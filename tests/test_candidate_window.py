from __future__ import annotations

import json
import sqlite3

from condom_core.candidate_window import (
    insert_x_returned_candidates,
    read_candidate_window_rows,
    rebuild_candidate_window,
)
from condom_core.db import init_db, insert_raw_network_response, upsert_items
from condom_core.parse_x import parse_response, parse_response_ordered


def _timeline_entries(*tweets: dict) -> dict:
    return {
        "data": {
            "home": {
                "home_timeline_urt": {
                    "instructions": [
                        {
                            "entries": [
                                {
                                    "content": {
                                        "itemContent": {
                                            "tweet_results": {"result": tweet},
                                        }
                                    }
                                }
                                for tweet in tweets
                            ]
                        }
                    ]
                }
            }
        }
    }


def _tweet(
    *,
    rest_id: str,
    text: str,
    handle: str = "author",
) -> dict:
    user = {
        "core": {"screen_name": handle, "name": handle.title()},
        "legacy": {"screen_name": handle, "name": handle.title(), "description": None},
    }
    return {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "legacy": {
            "id_str": rest_id,
            "full_text": text,
            "favorite_count": 1,
            "retweet_count": 0,
            "reply_count": 0,
            "quote_count": 0,
        },
        "core": {"user_results": {"result": user}},
    }


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _store_raw_response(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    response_id: str,
    body: dict,
    captured_at: str,
    url: str = "https://x.com/i/api/graphql/HomeTimeline",
) -> list[tuple[str, int]]:
    """Mirror ingest_raw_response candidate-window spine without events."""
    source = "x_for_you"
    ordered = parse_response_ordered(body, session_id=session_id, source=source)
    items = [item for item, _ in ordered]
    body_text = json.dumps(body, ensure_ascii=True)
    insert_raw_network_response(
        conn,
        response_id=response_id,
        session_id=session_id,
        url=url,
        body=body_text,
        parsed_ok=True,
        parsed_count=len(items),
        error=None,
        captured_at=captured_at,
    )
    if items:
        upsert_items(conn, items, captured_at)
    if ordered:
        insert_x_returned_candidates(
            conn,
            session_id=session_id,
            response_id=response_id,
            ordered=ordered,
            source=source,
            captured_at=captured_at,
        )
        rebuild_candidate_window(conn, session_id, source=source)
    return [(item.item_id, rank) for item, rank in ordered]


def test_parse_response_ordered_one_based_ranks_and_dedupes():
    body = _timeline_entries(
        _tweet(rest_id="9001", text="first"),
        _tweet(rest_id="9002", text="second"),
        _tweet(rest_id="9001", text="duplicate id"),
    )
    ordered = parse_response_ordered(body, session_id="sess-cw", source="x_for_you")

    assert [(item.item_id, rank) for item, rank in ordered] == [
        ("9001", 1),
        ("9002", 2),
    ]
    assert [item.item_id for item in parse_response(body, session_id="sess-cw")] == [
        "9001",
        "9002",
    ]


def test_x_returned_candidates_rows_per_response():
    conn = _memory_conn()
    session_id = "sess-xrc"
    body = _timeline_entries(
        _tweet(rest_id="100", text="alpha"),
        _tweet(rest_id="200", text="beta"),
    )
    _store_raw_response(
        conn,
        session_id=session_id,
        response_id="resp-1",
        body=body,
        captured_at="2026-06-21T10:00:00+00:00",
    )

    rows = conn.execute(
        """
        SELECT item_id, response_rank
        FROM x_returned_candidates
        WHERE response_id = ?
        ORDER BY response_rank ASC
        """,
        ("resp-1",),
    ).fetchall()

    assert [(row["item_id"], row["response_rank"]) for row in rows] == [
        ("100", 1),
        ("200", 2),
    ]


def test_rebuild_candidate_window_first_occurrence_merge_order():
    conn = _memory_conn()
    session_id = "sess-merge"

    _store_raw_response(
        conn,
        session_id=session_id,
        response_id="resp-b",
        body=_timeline_entries(
            _tweet(rest_id="200", text="seen in b first here"),
            _tweet(rest_id="400", text="only in b"),
        ),
        captured_at="2026-06-21T11:00:00+00:00",
    )
    _store_raw_response(
        conn,
        session_id=session_id,
        response_id="resp-a",
        body=_timeline_entries(
            _tweet(rest_id="100", text="only in a"),
            _tweet(rest_id="200", text="seen in a first"),
            _tweet(rest_id="300", text="only in a tail"),
        ),
        captured_at="2026-06-21T10:00:00+00:00",
    )

    window = read_candidate_window_rows(conn, session_id)
    assert [row["item_id"] for row in window] == ["100", "200", "300", "400"]
    assert [row["window_rank"] for row in window] == [1, 2, 3, 4]
    assert window[1]["first_response_id"] == "resp-a"
    assert window[3]["first_response_id"] == "resp-b"


def test_candidate_window_includes_unexposed_candidates_without_events():
    conn = _memory_conn()
    session_id = "sess-unexposed"

    _store_raw_response(
        conn,
        session_id=session_id,
        response_id="resp-only",
        body=_timeline_entries(
            _tweet(rest_id="501", text="never scrolled"),
            _tweet(rest_id="502", text="also unexposed"),
        ),
        captured_at="2026-06-21T09:00:00+00:00",
    )

    event_count = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchone()["n"]
    window = read_candidate_window_rows(conn, session_id)

    assert event_count == 0
    assert [row["item_id"] for row in window] == ["501", "502"]
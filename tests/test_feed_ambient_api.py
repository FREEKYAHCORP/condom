from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from condom_core.ambient_m3 import (
    DEFAULT_IMMEDIATE_SNAPSHOT_KIND,
    build_immediate_feed_snapshot,
    request_m3_scoring_batches,
)
from condom_core.api.app import app
from condom_core.db import init_db, insert_raw_network_response, upsert_items
from condom_core.candidate_window import rebuild_candidate_window
from condom_core.models import Item


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_window(conn: sqlite3.Connection, session_id: str = "sess-a") -> list[str]:
    harvested = "2026-01-01T00:00:00+00:00"
    items = [
        Item(
            item_id="t1",
            source="x_graphql",
            session_id=session_id,
            batch_id="b0",
            original_rank=1,
            author_handle="a",
            author_name="A",
            text="ml agents",
            raw_json={},
        ),
        Item(
            item_id="t2",
            source="x_graphql",
            session_id=session_id,
            batch_id="b0",
            original_rank=2,
            author_handle="b",
            author_name="B",
            text="sports",
            raw_json={},
        ),
    ]
    upsert_items(conn, items, harvested)
    insert_raw_network_response(
        conn,
        response_id="resp-1",
        session_id=session_id,
        url="https://x.com/i/api/graphql/HomeTimeline",
        body="{}",
        parsed_ok=True,
        parsed_count=len(items),
        error=None,
        captured_at=harvested,
    )
    conn.execute(
        """
        INSERT INTO x_returned_candidates (
          response_id, session_id, item_id, response_rank, source, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("resp-1", session_id, "t1", 1, "x_graphql", harvested),
    )
    conn.execute(
        """
        INSERT INTO x_returned_candidates (
          response_id, session_id, item_id, response_rank, source, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("resp-1", session_id, "t2", 2, "x_graphql", harvested),
    )
    conn.commit()
    rebuild_candidate_window(conn, session_id, source="x_graphql")
    return ["t1", "t2"]


def _fake_model_json(ids: list[str]) -> str:
    payload = {
        "items": [
            {
                "item_id": i,
                "score": 90.0 if i == "t1" else 40.0,
                "tier": "high" if i == "t1" else "low",
                "serve": i == "t1",
                "reason": "test",
            }
            for i in ids
        ]
    }
    return json.dumps(payload)

def test_request_m3_scoring_batches_persists_scores_and_snapshot(monkeypatch):
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    conn = _memory_conn()
    _seed_window(conn)
    result = request_m3_scoring_batches(
        conn,
        "sess-a",
        batch_size=8,
        max_batches=1,
        model_call=lambda _prompt: _fake_model_json(["t1", "t2"]),
    )
    assert result["items_scored"] == 2
    assert result["m3_status"] in {"idle", "ready"}
    assert len(result["model_call_ids"]) == 1
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ambient_m3_item_scores WHERE session_id=?",
        ("sess-a",),
    ).fetchone()
    assert row["n"] == 2
    snap = build_immediate_feed_snapshot(conn, "sess-a", persist=False)
    assert snap["items"][0]["item_id"] == "t1"
    assert snap["items"][0]["m3_score"] == 90.0


def test_feed_endpoints_status_current_and_async_request(monkeypatch, tmp_path):
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    db_path = tmp_path / "api.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_window(conn, "sess-api")
    conn.close()

    def _conn_override():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        return c

    ran: list[dict] = []

    def _sync_background(**kwargs):
        ran.append(kwargs)
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        request_m3_scoring_batches(
            c,
            kwargs["session_id"],
            batch_size=kwargs["batch_size"],
            max_batches=kwargs["max_batches"],
            model_call=lambda _prompt: _fake_model_json(["t1", "t2"]),
        )
        c.close()

    monkeypatch.setattr("condom_core.api.app.connect", lambda path=None: _conn_override())
    monkeypatch.setattr("condom_core.api.app.DB_PATH", db_path)
    monkeypatch.setattr(
        "condom_core.api.app.schedule_m3_scoring_background",
        lambda **kwargs: _sync_background(**kwargs),
    )

    client = TestClient(app)

    status = client.get("/feed/status", params={"session_id": "sess-api"})
    assert status.status_code == 200
    body = status.json()
    assert body["candidate_count"] == 2
    assert body["unscored_count"] == 2

    current = client.get("/feed/m3/current", params={"session_id": "sess-api", "limit": 10})
    assert current.status_code == 200
    cur = current.json()
    assert cur["kind"] == DEFAULT_IMMEDIATE_SNAPSHOT_KIND
    assert len(cur["items"]) == 2
    assert cur["items"][0]["m3_score"] is None

    req = client.post(
        "/feed/m3/request",
        json={"session_id": "sess-api", "batch_size": 8, "max_batches": 1},
    )
    assert req.status_code == 200
    sched = req.json()
    assert sched["scheduled"] is True
    assert len(ran) == 1

    after = client.get("/feed/m3/current", params={"session_id": "sess-api"})
    assert after.status_code == 200
    items = after.json()["items"]
    assert items[0]["item_id"] == "t1"
    assert items[0]["m3_score"] == 90.0
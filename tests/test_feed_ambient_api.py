from __future__ import annotations

import json
import sqlite3
import time

import pytest
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


def _phased_ambient_m3_enabled() -> bool:
    from condom_core.ambient_m3 import load_feed_status

    conn = _memory_conn()
    status = load_feed_status(conn, "unused-session")
    return "active_window_max" in status


def _seed_many_candidates(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    count: int,
    active_window_max: int = 250,
) -> list[str]:
    """Seed more candidates than the active feed window to assert bounded serving."""
    harvested = "2026-01-01T00:00:00+00:00"
    ids = [f"c{i}" for i in range(1, count + 1)]
    items = [
        Item(
            item_id=item_id,
            source="x_graphql",
            session_id=session_id,
            batch_id="b0",
            original_rank=rank,
            author_handle=f"user{rank}",
            author_name=f"User {rank}",
            text=f"topic {rank}",
            link_url=f"https://x.com/i/status/{item_id}",
            raw_json={},
        )
        for rank, item_id in enumerate(ids, start=1)
    ]
    upsert_items(conn, items, harvested)
    insert_raw_network_response(
        conn,
        response_id="resp-bulk",
        session_id=session_id,
        url="https://x.com/i/api/graphql/HomeTimeline",
        body="{}",
        parsed_ok=True,
        parsed_count=len(items),
        error=None,
        captured_at=harvested,
    )
    for rank, item_id in enumerate(ids, start=1):
        conn.execute(
            """
            INSERT INTO x_returned_candidates (
              response_id, session_id, item_id, response_rank, source, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("resp-bulk", session_id, item_id, rank, "x_graphql", harvested),
        )
    conn.commit()
    rebuild_candidate_window(conn, session_id, source="x_graphql")
    return ids


def _fake_model_json_scored(ids: list[str], *, base_score: float = 100.0) -> str:
    payload = {
        "items": [
            {
                "item_id": i,
                "score": base_score - float(idx),
                "tier": "high" if idx < 3 else "mid",
                "serve": True,
                "reason": "test",
            }
            for idx, i in enumerate(ids)
        ]
    }
    return json.dumps(payload)


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

def test_feed_m3_request_schema_defaults_batch_and_max_batches():
    from condom_core.api.schemas import FeedM3RequestIn

    body = FeedM3RequestIn(session_id="sess-defaults")
    assert body.batch_size == 50
    assert body.max_batches == 5


def test_feed_m3_request_api_uses_schema_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    db_path = tmp_path / "defaults.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_window(conn, "sess-def")
    conn.close()

    captured: list[dict] = []

    def _conn_override():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        return c

    def _capture_background(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("condom_core.api.app.connect", lambda path=None: _conn_override())
    monkeypatch.setattr("condom_core.api.app.DB_PATH", db_path)
    monkeypatch.setattr(
        "condom_core.api.app.schedule_m3_scoring_background",
        _capture_background,
    )

    client = TestClient(app)
    resp = client.post("/feed/m3/request", json={"session_id": "sess-def"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scheduled"] is True
    assert data["batch_size"] == 50
    assert data["max_batches"] == 5
    assert captured[0]["batch_size"] == 50
    assert captured[0]["max_batches"] == 5


def test_phased_feed_status_active_window_fields(monkeypatch, tmp_path):
    if not _phased_ambient_m3_enabled():
        pytest.skip("phased ambient_m3 status fields not landed yet")
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    db_path = tmp_path / "phased-status.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    total = 300
    _seed_many_candidates(conn, "sess-phased", count=total)
    conn.close()

    def _conn_override():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        return c

    monkeypatch.setattr("condom_core.api.app.connect", lambda path=None: _conn_override())
    monkeypatch.setattr("condom_core.api.app.DB_PATH", db_path)

    client = TestClient(app)
    body = client.get("/feed/status", params={"session_id": "sess-phased"}).json()
    assert body["total_seen_count"] == total
    assert body["active_window_max"] == 250
    assert body["top_k"] == 10
    assert body["candidate_count"] <= 250
    assert body["expired_count"] == total - body["candidate_count"]
    assert body["epoch_status"] in {
        "warming",
        "scoring_top",
        "top_ready",
        "scoring_rest",
        "complete",
        "unavailable",
    }
    assert "top_ready" in body
    assert "latest_graphql_at" in body
    assert "latest_m3_score_at" in body
    assert "latest_feed_snapshot_at" in body


def test_phased_feed_current_active_window_metadata_and_sort(monkeypatch, tmp_path):
    if not _phased_ambient_m3_enabled():
        pytest.skip("phased ambient_m3 current feed not landed yet")
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    db_path = tmp_path / "phased-current.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    ids = _seed_many_candidates(conn, "sess-cur", count=12)
    conn.close()

    def _conn_override():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        return c

    def _sync_score(**kwargs):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        batch_ids = ids[:5]
        request_m3_scoring_batches(
            c,
            kwargs["session_id"],
            batch_size=kwargs["batch_size"],
            max_batches=kwargs["max_batches"],
            model_call=lambda _p: _fake_model_json_scored(batch_ids),
        )
        c.close()

    monkeypatch.setattr("condom_core.api.app.connect", lambda path=None: _conn_override())
    monkeypatch.setattr("condom_core.api.app.DB_PATH", db_path)
    monkeypatch.setattr("condom_core.api.app.schedule_m3_scoring_background", _sync_score)

    client = TestClient(app)
    before = client.get("/feed/m3/current", params={"session_id": "sess-cur"}).json()
    assert len(before["items"]) <= 12
    first = before["items"][0]
    for key in ("item_id", "rank", "m3_score", "tier", "serve", "reason", "author_handle"):
        assert key in first

    client.post(
        "/feed/m3/request",
        json={"session_id": "sess-cur", "batch_size": 5, "max_batches": 1},
    )
    after = client.get("/feed/m3/current", params={"session_id": "sess-cur"}).json()
    scored = [it for it in after["items"] if it.get("m3_score") is not None]
    unscored = [it for it in after["items"] if it.get("m3_score") is None]
    assert scored
    assert scored[0]["m3_score"] >= scored[-1]["m3_score"]
    if unscored:
        assert after["items"].index(unscored[0]) > after["items"].index(scored[-1])
    assert after["items"][0].get("text") or after["items"][0].get("author_handle")


def test_phased_top_ready_after_first_scored_batch(monkeypatch, tmp_path):
    if not _phased_ambient_m3_enabled():
        pytest.skip("phased top_ready progression not landed yet")
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")
    db_path = tmp_path / "phased-top.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    ids = _seed_many_candidates(conn, "sess-top", count=15)
    conn.close()

    def _conn_override():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_db(c)
        return c

    monkeypatch.setattr("condom_core.api.app.connect", lambda path=None: _conn_override())
    monkeypatch.setattr("condom_core.api.app.DB_PATH", db_path)

    client = TestClient(app)

    def score_batch(batch_n: int) -> None:
        start = (batch_n - 1) * 5
        batch_ids = ids[start : start + 5]

        def _sync(**kwargs):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            init_db(c)
            request_m3_scoring_batches(
                c,
                kwargs["session_id"],
                batch_size=5,
                max_batches=1,
                model_call=lambda _p: _fake_model_json_scored(batch_ids),
            )
            c.close()

        monkeypatch.setattr("condom_core.api.app.schedule_m3_scoring_background", _sync)
        client.post(
            "/feed/m3/request",
            json={"session_id": "sess-top", "batch_size": 5, "max_batches": 1},
        )
        time.sleep(0.05)

    score_batch(1)
    st1 = client.get("/feed/status", params={"session_id": "sess-top"}).json()
    assert st1["scored_count"] == 5
    assert st1["top_ready"] is False
    assert st1["epoch_status"] in {"warming", "scoring_top", "scoring_rest"}

    score_batch(2)
    st2 = client.get("/feed/status", params={"session_id": "sess-top"}).json()
    assert st2["scored_count"] >= 10
    assert st2["top_ready"] is True
    assert st2["epoch_status"] in {"top_ready", "scoring_rest", "complete"}

    cur = client.get("/feed/m3/current", params={"session_id": "sess-top", "limit": 10}).json()
    assert len(cur["items"]) == 10
    assert all(it.get("m3_score") is not None for it in cur["items"])
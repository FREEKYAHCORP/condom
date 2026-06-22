from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from condom_core.ambient_m3 import (
    get_feed_current,
    load_feed_status,
    schedule_m3_scoring_background,
)
from condom_core.api.rank_service import ensure_ranked
from condom_core.api.schemas import EventsIn, FeedM3RequestIn, ItemsIn, RawResponseIn
from condom_core.config import DB_PATH
from condom_core.db import connect, init_db
from condom_core.ingest import ingest_dom_items, ingest_events, ingest_raw_response, rebuild_session_order
from condom_core.minimax_client import get_key

app = FastAPI(title="Condom Core", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _conn():
    conn = connect()
    init_db(conn)
    return conn


@app.get("/health")
def health():
    return {"ok": True, "db": str(DB_PATH), "minimax_key_present": bool(get_key())}


@app.post("/ingest/raw-response")
def raw_response(payload: RawResponseIn):
    conn = _conn()
    return ingest_raw_response(
        conn,
        session_id=payload.session_id,
        response_id=payload.response_id,
        url=payload.url,
        body=payload.body,
        captured_at=payload.captured_at,
    )


@app.post("/ingest/items")
def items(payload: ItemsIn):
    conn = _conn()
    count = ingest_dom_items(conn, payload.session_id, payload.items)
    rebuild_session_order(conn, payload.session_id)
    return {"ok": True, "inserted_items": count}


@app.post("/ingest/events")
def events(payload: EventsIn):
    conn = _conn()
    count = ingest_events(conn, payload.session_id, payload.events)
    return {"ok": True, "inserted_events": count}


@app.get("/rank")
def rank(
    session_id: str,
    mode: str = Query(default="native", pattern="^(native|cheap|m3)$"),
    refresh: bool = False,
):
    conn = _conn()
    return ensure_ranked(conn, session_id, mode, refresh=refresh)  # type: ignore[arg-type]


@app.get("/feed/status")
def feed_status(session_id: str = Query(..., min_length=1)):
    conn = _conn()
    try:
        return load_feed_status(conn, session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/feed/m3/current")
def feed_m3_current(
    session_id: str = Query(..., min_length=1),
    limit: int | None = Query(default=None, ge=1, le=500),
):
    conn = _conn()
    try:
        return get_feed_current(conn, session_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/feed/m3/request")
def feed_m3_request(payload: FeedM3RequestIn, background_tasks: BackgroundTasks):
    conn = _conn()
    try:
        status = load_feed_status(conn, payload.session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if status.get("unscored_count", 0) <= 0:
        return {
            "ok": True,
            "scheduled": False,
            "session_id": payload.session_id,
            "reason": "no_unscored_candidates",
            "status": status,
        }
    background_tasks.add_task(
        schedule_m3_scoring_background,
        db_path=str(DB_PATH),
        session_id=payload.session_id,
        batch_size=payload.batch_size,
        max_batches=payload.max_batches,
    )
    status = load_feed_status(conn, payload.session_id)
    return {
        "ok": True,
        "scheduled": True,
        "session_id": payload.session_id,
        "batch_size": payload.batch_size,
        "max_batches": payload.max_batches,
        "status": status,
    }

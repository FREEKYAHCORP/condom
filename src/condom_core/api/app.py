from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from condom_core.api.rank_service import ensure_ranked
from condom_core.api.schemas import EventsIn, ItemsIn, RawResponseIn
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

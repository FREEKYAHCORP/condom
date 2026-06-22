from __future__ import annotations
import hashlib
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from condom_core.ambient_m3 import (
    get_feed_current,
    load_feed_status,
    schedule_m3_scoring_background,
)
from condom_core.api.rank_service import ensure_ranked
from condom_core.api.schemas import (
    EventsIn,
    FeedM3RequestIn,
    ItemsIn,
    ProfileResetIn,
    ProfileUpdateIn,
    RawResponseIn,
)
from condom_core.config import DB_PATH
from condom_core.db import connect, init_db
from condom_core.ingest import ingest_dom_items, ingest_events, ingest_raw_response, rebuild_session_order
from condom_core.profile import (
    build_profile_prompt_preview,
    compile_ambient_m3_prompt_fields,
    get_active_profile_version,
    load_user_profile_store,
    reset_user_profile,
    save_profile_version,
)
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
        return load_feed_status(conn, session_id, db_path=str(DB_PATH))
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
def feed_m3_request(payload: FeedM3RequestIn):
    conn = _conn()
    try:
        status = load_feed_status(conn, payload.session_id, db_path=str(DB_PATH))
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
    schedule_m3_scoring_background(
        db_path=str(DB_PATH),
        session_id=payload.session_id,
        batch_size=payload.batch_size,
        max_batches=payload.max_batches,
    )
    status = load_feed_status(conn, payload.session_id, db_path=str(DB_PATH))
    return {
        "ok": True,
        "scheduled": True,
        "session_id": payload.session_id,
        "batch_size": payload.batch_size,
        "max_batches": payload.max_batches,
        "status": status,
    }


def _profile_version_summary(store: dict[str, Any]) -> list[dict[str, Any]]:
    active_id = store.get("active_version_id")
    summaries: list[dict[str, Any]] = []
    for entry in store.get("versions") or []:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("profile_version_id")
        summaries.append(
            {
                "profile_version_id": vid,
                "created_at": entry.get("created_at"),
                "source": entry.get("source"),
                "active": vid == active_id,
            }
        )
    return summaries


def _profile_save_response(version: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "active_version_id": version["profile_version_id"],
        "version": version,
        "active": version,
    }


@app.get("/profile")
def profile_get():
    store = load_user_profile_store()
    active = get_active_profile_version(store)
    return {
        "ok": True,
        "active_version_id": store["active_version_id"],
        "active": active,
        "versions": _profile_version_summary(store),
    }


@app.put("/profile")
def profile_put(payload: ProfileUpdateIn):
    fields = payload.model_dump(exclude={"source"}, exclude_none=True)
    source = payload.source or "extension"
    version = save_profile_version(fields, source=source)
    return _profile_save_response(version)


@app.post("/profile/reset")
def profile_reset(payload: ProfileResetIn | None = None):
    source = (payload.source if payload else None) or "extension"
    version = reset_user_profile(source=source)
    return _profile_save_response(version)


@app.get("/profile/prompt-preview")
def profile_prompt_preview(
    state_preamble: str | None = Query(default=None),
    identity_revealed: str | None = Query(default=None),
    identity_endorsed: str | None = Query(default=None),
    positive_profile: str | None = Query(default=None),
    negative_profile: str | None = Query(default=None),
):
    draft_keys = (
        state_preamble,
        identity_revealed,
        identity_endorsed,
        positive_profile,
        negative_profile,
    )
    if any(v is not None for v in draft_keys):
        active = get_active_profile_version()
        draft = dict(active)
        if state_preamble is not None:
            draft["state_preamble"] = state_preamble
        if identity_revealed is not None:
            draft["identity_revealed"] = identity_revealed
        if identity_endorsed is not None:
            draft["identity_endorsed"] = identity_endorsed
        if positive_profile is not None:
            draft["positive_profile"] = positive_profile
        if negative_profile is not None:
            draft["negative_profile"] = negative_profile
        from condom_core.prompts import (
            AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION,
            build_ambient_m3_item_score_prompt,
        )

        fields = compile_ambient_m3_prompt_fields(draft)
        prompt = build_ambient_m3_item_score_prompt(
            "(no candidates — preview only)",
            identity_revealed=fields["identity_revealed"],
            identity_endorsed=fields["identity_endorsed"],
            state_preamble=fields["state_preamble"],
            negative_profile=fields["negative_profile"],
        )
        return {
            "ok": True,
            "profile_version_id": draft.get("profile_version_id"),
            "prompt_version": AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION,
            "prompt_hash": f"sha256:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}",
            "fields": fields,
            "prompt": prompt,
            "prompt_text": prompt,
        }
    preview = build_profile_prompt_preview()
    return {
        "ok": True,
        **preview,
        "prompt_text": preview.get("prompt", ""),
    }

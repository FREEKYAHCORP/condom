"""Ambient M3 item scoring and immediate feed snapshots (no synchronous /rank M3)."""

from __future__ import annotations

import json
import threading
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .feed_generation import ModelCallResult, _normalize_model_call_result, load_joined_candidate_rows
from .minimax_client import call_minimax, get_key
from .parse_llm_outputs import parse_ambient_m3_items_json
from .prompts import AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION, build_ambient_m3_item_score_prompt

AMBIENT_M3_ARM = "ambient_m3_item_score_v0"
AMBIENT_M3_MODEL = "MiniMax-M3"
DEFAULT_IMMEDIATE_SNAPSHOT_KIND = "immediate"
AMBIENT_M3_BATCH_ID = "candidate_window"

DEFAULT_ACTIVE_WINDOW_MAX = 250
DEFAULT_TOP_K = 10
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_BATCHES = 5

PhaseStatus = str  # warming|scoring_top|top_ready|scoring_rest|complete|unavailable

ModelCall = Callable[[str], ModelCallResult]

_BACKGROUND_LOCK = threading.Lock()
_RUNNING_BACKGROUND_KEYS: set[str] = set()
_RUNNING_BACKGROUND_SESSIONS: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None

def _background_session_key(db_path: str, session_id: str) -> str:
    return f"{db_path}:{session_id}"


def _is_background_running(db_path: str | None, session_id: str) -> bool:
    with _BACKGROUND_LOCK:
        if session_id in _RUNNING_BACKGROUND_SESSIONS:
            return True
    if not db_path:
        return False
    key = _background_session_key(db_path, session_id)
    with _BACKGROUND_LOCK:
        return key in _RUNNING_BACKGROUND_KEYS


def count_total_seen_items(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT item_id) AS n FROM x_returned_candidates WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"] or 0)




def load_active_window_rows(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Latest active_window_max candidates by first_captured_at desc, window_rank asc."""
    joined = load_joined_candidate_rows(conn, session_id, source=source)
    if not joined:
        return []
    ordered = sorted(
        joined,
        key=lambda r: (
            -_capture_sort_key(str(r.get("first_captured_at") or "")),
            int(r.get("window_rank") or 10**9),
        ),
    )
    return ordered[: max(0, active_window_max)]


def _capture_sort_key(captured_at: str) -> float:
    if not captured_at:
        return 0.0
    try:
        return datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0

def _active_window_metrics(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    total_seen = count_total_seen_items(conn, session_id)
    full_window = count_candidate_items(conn, session_id)
    active_rows = load_active_window_rows(conn, session_id, active_window_max=active_window_max)
    active_ids = {str(r["item_id"]) for r in active_rows}
    active_count = len(active_rows)
    scores = load_ambient_score_map(conn, session_id)
    scored_in_active = sum(1 for iid in active_ids if iid in scores)
    unscored_in_active = max(0, active_count - scored_in_active)
    expired_count = max(0, full_window - active_count)
    shelf_n = min(top_k, active_count) if active_count else 0
    top_ready = shelf_n > 0 and scored_in_active >= shelf_n
    return {
        "total_seen_count": total_seen,
        "full_window_count": full_window,
        "active_count": active_count,
        "expired_count": expired_count,
        "scored_in_active": scored_in_active,
        "unscored_in_active": unscored_in_active,
        "top_ready": top_ready,
        "shelf_n": shelf_n,
        "active_rows": active_rows,
        "scores": scores,
    }


def _derive_phase_status(
    *,
    base_availability: str,
    active_count: int,
    unscored_in_active: int,
    top_ready: bool,
    background_running: bool,
) -> PhaseStatus:
    if base_availability == "unavailable":
        return "unavailable"
    if active_count <= 0:
        return "warming"
    if unscored_in_active <= 0:
        return "complete"
    if not top_ready:
        return "scoring_top"
    if background_running:
        return "scoring_rest"
    return "top_ready"


def _item_public_url(row: dict[str, Any]) -> str | None:
    link = row.get("link_url")
    if link:
        return str(link)
    handle = row.get("author_handle")
    item_id = row.get("item_id")
    if handle and item_id and str(item_id).isdigit():
        h = str(handle).lstrip("@")
        return f"https://x.com/{h}/status/{item_id}"
    return None


def _feed_item_from_row(
    row: dict[str, Any],
    *,
    rank: int,
    entry: dict[str, Any] | None,
) -> dict[str, Any]:
    item_id = str(row["item_id"])
    payload: dict[str, Any] = {
        "item_id": item_id,
        "rank": rank,
        "snapshot_rank": rank,
        "window_rank": int(row.get("window_rank") or 0),
        "score": None if entry is None else entry["score"],
        "m3_score": None if entry is None else entry["score"],
        "tier": None if entry is None else entry["tier"],
        "serve": None if entry is None else entry["serve"],
        "reason": None if entry is None else entry["reason"],
        "author_handle": row.get("author_handle"),
        "text": row.get("text") or row.get("rendered_text"),
        "url": _item_public_url(row),
    }
    return payload




def count_candidate_items(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM candidate_window_items WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"] or 0)


def count_scored_items(conn: sqlite3.Connection, session_id: str) -> int:
    if not _table_exists(conn, "ambient_m3_item_scores"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ambient_m3_item_scores WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"] or 0)

def load_unscored_candidate_rows(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
) -> list[dict[str, Any]]:
    """Unscored rows within the active candidate window only."""
    active = load_active_window_rows(conn, session_id, active_window_max=active_window_max)
    if not active:
        return []
    scores = load_ambient_score_map(conn, session_id)
    unscored = [r for r in active if str(r["item_id"]) not in scores]
    unscored.sort(key=lambda r: int(r.get("window_rank") or 10**9))
    if limit is not None:
        return unscored[:limit]
    return unscored


def load_feed_status(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    db_path: str | None = None,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    metrics = _active_window_metrics(
        conn, session_id, active_window_max=active_window_max, top_k=top_k
    )
    base_status, m3_error = _m3_availability()
    background_running = _is_background_running(db_path, session_id)
    unscored_in_active = int(metrics["unscored_in_active"])
    if background_running and base_status != "unavailable":
        m3_status = "running"
    elif unscored_in_active <= 0:
        m3_status = "idle" if base_status != "unavailable" else "unavailable"
    else:
        m3_status = base_status
    phase = _derive_phase_status(
        base_availability=base_status,
        active_count=int(metrics["active_count"]),
        unscored_in_active=unscored_in_active,
        top_ready=bool(metrics["top_ready"]),
        background_running=background_running,
    )
    return {
        "session_id": session_id,
        "total_seen_count": int(metrics["total_seen_count"]),
        "candidate_count": int(metrics["active_count"]),
        "expired_count": int(metrics["expired_count"]),
        "active_window_max": active_window_max,
        "top_k": top_k,
        "top_ready": bool(metrics["top_ready"]),
        "phase": phase,
        "epoch_status": phase,
        "scored_count": int(metrics["scored_in_active"]),
        "unscored_count": unscored_in_active,
        "latest_graphql_at": _latest_graphql_at(conn, session_id),
        "latest_m3_score_at": _latest_m3_score_at(conn, session_id),
        "latest_feed_snapshot_at": _latest_feed_snapshot_at(conn, session_id),
        "m3_queue_depth": unscored_in_active,
        "m3_status": m3_status,
        "m3_error": m3_error,
    }


def _latest_graphql_at(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(captured_at) AS ts
        FROM raw_network_responses
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None or row["ts"] is None:
        return None
    return str(row["ts"])


def _latest_m3_score_at(conn: sqlite3.Connection, session_id: str) -> str | None:
    if not _table_exists(conn, "ambient_m3_item_scores"):
        return None
    row = conn.execute(
        """
        SELECT MAX(scored_at) AS ts
        FROM ambient_m3_item_scores
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None or row["ts"] is None:
        return None
    return str(row["ts"])


def _latest_feed_snapshot_at(conn: sqlite3.Connection, session_id: str) -> str | None:
    if not _table_exists(conn, "feed_snapshots"):
        return None
    row = conn.execute(
        """
        SELECT MAX(created_at) AS ts
        FROM feed_snapshots
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None or row["ts"] is None:
        return None
    return str(row["ts"])


def _m3_availability() -> tuple[str, str | None]:
    if not get_key():
        return "unavailable", "missing MiniMax API key"
    return "ready", None


def render_ambient_candidate_batch_text(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        item_id = row["item_id"]
        rendered = (row.get("rendered_text") or "").strip()
        lines.append(f"<{item_id}>\n{rendered}")
    return "\n\n".join(lines)


def build_m3_item_scoring_prompt(
    rows: list[dict[str, Any]],
    *,
    identity_revealed: str = "",
    identity_endorsed: str = "",
    state_preamble: str = "ordinary scroll session. a few minutes to look around.",
) -> str:
    candidate_ids = [str(r["item_id"]) for r in rows]
    prompt = build_ambient_m3_item_score_prompt(
        render_ambient_candidate_batch_text(rows),
        identity_revealed=identity_revealed,
        identity_endorsed=identity_endorsed,
        state_preamble=state_preamble,
    )
    validate_m3_item_scoring_prompt(prompt, candidate_ids)
    return prompt


def validate_m3_item_scoring_prompt(prompt: str, candidate_ids: list[str]) -> None:
    for cid in candidate_ids:
        if f"<{cid}>" not in prompt and cid not in prompt:
            raise ValueError(f"candidate id {cid!r} missing from prompt")


def insert_model_call_row(
    conn: sqlite3.Connection,
    *,
    call_id: str,
    session_id: str,
    prompt: str,
    response_text: str | None,
    parsed_ok: int,
    error: str | None,
    latency_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO model_calls (
          call_id, arm, session_id, batch_id, model_name, prompt_version,
          request_json, response_text, parsed_ok, error, latency_ms,
          input_tokens, output_tokens, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            AMBIENT_M3_ARM,
            session_id,
            AMBIENT_M3_BATCH_ID,
            AMBIENT_M3_MODEL,
            AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION,
            json.dumps({"prompt": prompt}, ensure_ascii=True),
            response_text,
            parsed_ok,
            error,
            latency_ms,
            input_tokens,
            output_tokens,
            _utc_now(),
        ),
    )


def upsert_ambient_m3_scores(
    conn: sqlite3.Connection,
    session_id: str,
    scored: list[dict[str, Any]],
    *,
    model_call_id: str | None,
    scored_at: str | None = None,
) -> int:
    ts = scored_at or _utc_now()
    for row in scored:
        conn.execute(
            """
            INSERT OR REPLACE INTO ambient_m3_item_scores (
              session_id, item_id, score, tier, serve, reason, model_call_id, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                row["item_id"],
                float(row["score"]),
                row["tier"],
                1 if row["serve"] else 0,
                row["reason"],
                model_call_id,
                ts,
            ),
        )
    conn.commit()
    return len(scored)


def load_ambient_score_map(conn: sqlite3.Connection, session_id: str) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "ambient_m3_item_scores"):
        return {}
    rows = conn.execute(
        """
        SELECT item_id, score, tier, serve, reason, model_call_id, scored_at
        FROM ambient_m3_item_scores
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row["item_id"])] = {
            "score": float(row["score"]),
            "tier": row["tier"],
            "serve": bool(row["serve"]),
            "reason": row["reason"],
            "model_call_id": row["model_call_id"],
            "scored_at": row["scored_at"],
        }
    return out


def _sort_key_for_snapshot(row: dict[str, Any], scores: dict[str, dict[str, Any]]) -> tuple[int, float, int]:
    item_id = str(row["item_id"])
    window_rank = int(row.get("window_rank") or 10**9)
    entry = scores.get(item_id)
    if entry is None:
        return (1, 0.0, window_rank)
    return (0, -float(entry["score"]), window_rank)


def build_immediate_feed_snapshot(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
    persist: bool = True,
    kind: str = DEFAULT_IMMEDIATE_SNAPSHOT_KIND,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
) -> dict[str, Any]:
    """Active window only: scored first by M3 score desc, unscored after."""
    metrics = _active_window_metrics(conn, session_id, active_window_max=active_window_max)
    active_rows: list[dict[str, Any]] = metrics["active_rows"]  # type: ignore[assignment]
    scores: dict[str, dict[str, Any]] = metrics["scores"]  # type: ignore[assignment]
    ordered = sorted(active_rows, key=lambda r: _sort_key_for_snapshot(r, scores))
    if limit is not None:
        ordered = ordered[:limit]
    active_count = len(active_rows)
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(ordered, start=1):
        item_id = str(row["item_id"])
        entry = scores.get(item_id)
        items.append(_feed_item_from_row(row, rank=rank, entry=entry))
    snapshot_id = f"{kind}:{session_id}:{datetime.now(timezone.utc).timestamp()}:{uuid.uuid4().hex}"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "snapshot_id": snapshot_id,
        "kind": kind,
        "arm": AMBIENT_M3_ARM,
        "effective_arm": AMBIENT_M3_ARM,
        "created_at": _utc_now(),
        "candidate_count": active_count,
        "active_window_max": active_window_max,
        "ordered_item_ids": [item["item_id"] for item in items],
        "items": items,
    }
    if persist:
        persist_feed_snapshot(
            conn,
            snapshot_id,
            session_id,
            kind,
            active_count,
            items,
            meta={"active_window_max": active_window_max},
        )
        payload["persisted"] = True
    else:
        payload["persisted"] = False
    return payload


def persist_feed_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
    session_id: str,
    kind: str,
    candidate_count: int,
    items: list[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> None:
    created_at = _utc_now()
    meta_json = json.dumps(meta or {}, ensure_ascii=True)
    conn.execute(
        """
        INSERT INTO feed_snapshots (
          snapshot_id, session_id, kind, candidate_count, meta_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (snapshot_id, session_id, kind, candidate_count, meta_json, created_at),
    )
    for item in items:
        conn.execute(
            """
            INSERT INTO feed_snapshot_items (
              snapshot_id, item_id, snapshot_rank, window_rank,
              m3_score, tier, serve, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                item["item_id"],
                int(item["snapshot_rank"]),
                int(item["window_rank"]),
                item.get("m3_score"),
                item.get("tier"),
                None if item.get("serve") is None else (1 if item["serve"] else 0),
                item.get("reason"),
            ),
        )
    conn.commit()


def _load_latest_snapshot_header(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    if not _table_exists(conn, "feed_snapshots"):
        return None
    return conn.execute(
        """
        SELECT snapshot_id, session_id, kind, candidate_count, meta_json, created_at
        FROM feed_snapshots
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


def _enrich_snapshot_feed_items(
    conn: sqlite3.Connection,
    session_id: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not items:
        return items
    ids = [str(it["item_id"]) for it in items]
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT cwi.item_id, cwi.window_rank,
               i.author_handle, i.text, i.rendered_text, i.link_url
        FROM candidate_window_items AS cwi
        JOIN items AS i ON i.item_id = cwi.item_id
        WHERE cwi.session_id = ? AND cwi.item_id IN ({placeholders})
        """,
        [session_id, *ids],
    ).fetchall()
    by_id = {str(r["item_id"]): dict(r) for r in rows}
    out: list[dict[str, Any]] = []
    for it in items:
        merged = dict(it)
        row = by_id.get(str(it["item_id"]))
        if row:
            merged["author_handle"] = row.get("author_handle")
            merged["text"] = row.get("text") or row.get("rendered_text")
            merged["url"] = _item_public_url(row)
        out.append(merged)
    return out


def get_feed_current(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
) -> dict[str, Any]:
    """Latest persisted immediate snapshot for active window, or rebuild. Never calls M3."""
    metrics = _active_window_metrics(conn, session_id, active_window_max=active_window_max)
    active_count = int(metrics["active_count"])
    header = _load_latest_snapshot_header(conn, session_id)
    if header is None:
        return build_immediate_feed_snapshot(
            conn, session_id, limit=limit, persist=True, active_window_max=active_window_max
        )
    latest_score_at = _latest_m3_score_at(conn, session_id)
    if int(header["candidate_count"]) != active_count:
        return build_immediate_feed_snapshot(
            conn, session_id, limit=limit, persist=True, active_window_max=active_window_max
        )
    if latest_score_at is not None and str(latest_score_at) > str(header["created_at"]):
        return build_immediate_feed_snapshot(
            conn, session_id, limit=limit, persist=True, active_window_max=active_window_max
        )
    snapshot_id = str(header["snapshot_id"])
    sql = """
        SELECT item_id, snapshot_rank, window_rank, m3_score, tier, serve, reason
        FROM feed_snapshot_items
        WHERE snapshot_id = ?
        ORDER BY snapshot_rank ASC
    """
    params: list[Any] = [snapshot_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    item_rows = conn.execute(sql, params).fetchall()
    items: list[dict[str, Any]] = []
    for row in item_rows:
        rank = int(row["snapshot_rank"])
        score = row["m3_score"]
        items.append(
            {
                "item_id": row["item_id"],
                "rank": rank,
                "snapshot_rank": rank,
                "window_rank": int(row["window_rank"]),
                "score": score,
                "m3_score": score,
                "tier": row["tier"],
                "serve": None if row["serve"] is None else bool(row["serve"]),
                "reason": row["reason"],
            }
        )
    items = _enrich_snapshot_feed_items(conn, session_id, items)
    return {
        "session_id": session_id,
        "snapshot_id": snapshot_id,
        "kind": header["kind"],
        "arm": AMBIENT_M3_ARM,
        "effective_arm": AMBIENT_M3_ARM,
        "created_at": header["created_at"],
        "candidate_count": active_count,
        "active_window_max": active_window_max,
        "ordered_item_ids": [item["item_id"] for item in items],
        "items": items,
    }


def score_m3_item_batch(
    conn: sqlite3.Connection,
    session_id: str,
    rows: list[dict[str, Any]],
    model_call: ModelCall,
) -> dict[str, Any]:
    """Score one batch; persists model_calls and ambient_m3_item_scores. No network unless model_call does."""
    if not rows:
        return {
            "items_scored": 0,
            "model_call_id": None,
            "parsed_ok": True,
            "error": None,
        }

    candidate_ids = [str(r["item_id"]) for r in rows]
    prompt = build_m3_item_scoring_prompt(rows)
    call_id = f"{AMBIENT_M3_ARM}:{session_id}:{datetime.now(timezone.utc).timestamp()}"

    raw = model_call(prompt)
    response_text, latency_ms, input_tokens, output_tokens, model_error = _normalize_model_call_result(raw)

    if model_error:
        insert_model_call_row(
            conn,
            call_id=call_id,
            session_id=session_id,
            prompt=prompt,
            response_text=response_text,
            parsed_ok=0,
            error=model_error,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        conn.commit()
        return {
            "items_scored": 0,
            "model_call_id": call_id,
            "parsed_ok": False,
            "error": model_error,
        }
    try:
        parsed = parse_ambient_m3_items_json(response_text or "", candidate_ids)
        insert_model_call_row(
            conn,
            call_id=call_id,
            session_id=session_id,
            prompt=prompt,
            response_text=response_text,
            parsed_ok=1,
            error=None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        n = upsert_ambient_m3_scores(conn, session_id, parsed, model_call_id=call_id)
        return {
            "items_scored": n,
            "model_call_id": call_id,
            "parsed_ok": True,
            "error": None,
        }
    except ValueError as exc:
        insert_model_call_row(
            conn,
            call_id=call_id,
            session_id=session_id,
            prompt=prompt,
            response_text=response_text,
            parsed_ok=0,
            error=str(exc),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        conn.commit()
        return {
            "items_scored": 0,
            "model_call_id": call_id,
            "parsed_ok": False,
            "error": str(exc),
        }


def request_m3_scoring_batches(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    model_call: ModelCall | None = None,
    rebuild_snapshot: bool = True,
    active_window_max: int = DEFAULT_ACTIVE_WINDOW_MAX,
) -> dict[str, Any]:
    """Sync: score up to max_batches unscored active-window rows."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_batches < 1:
        raise ValueError("max_batches must be >= 1")

    def _unscored_active() -> int:
        return len(load_unscored_candidate_rows(conn, session_id, active_window_max=active_window_max))

    if model_call is None:
        m3_status, m3_error = _m3_availability()
        if m3_status == "unavailable":
            unscored = _unscored_active()
            return {
                "session_id": session_id,
                "batches_requested": max_batches,
                "batches_completed": 0,
                "items_scored": 0,
                "unscored_remaining": unscored,
                "m3_status": "unavailable",
                "m3_error": m3_error,
                "model_call_ids": [],
                "snapshot": None,
            }
    else:
        m3_status, m3_error = "ready", None

    call_fn: ModelCall = model_call if model_call is not None else call_minimax
    batches_completed = 0
    items_scored = 0
    model_call_ids: list[str] = []
    last_error: str | None = None
    snapshot: dict[str, Any] | None = None

    for _ in range(max_batches):
        batch = load_unscored_candidate_rows(
            conn, session_id, limit=batch_size, active_window_max=active_window_max
        )
        if not batch:
            break
        result = score_m3_item_batch(conn, session_id, batch, call_fn)
        if result.get("model_call_id"):
            model_call_ids.append(str(result["model_call_id"]))
        if result.get("error"):
            last_error = str(result["error"])
            m3_status = "error"
            if result.get("items_scored", 0) <= 0:
                break
        batches_completed += 1
        batch_scored = int(result.get("items_scored") or 0)
        items_scored += batch_scored
        if rebuild_snapshot and batch_scored > 0:
            snapshot = build_immediate_feed_snapshot(
                conn, session_id, persist=True, active_window_max=active_window_max
            )
        if not result.get("parsed_ok") and batch_scored <= 0:
            break

    unscored_remaining = _unscored_active()
    if unscored_remaining == 0 and m3_status not in ("error", "unavailable"):
        m3_status = "idle"
    elif unscored_remaining > 0 and m3_status not in ("error", "unavailable"):
        m3_status = "ready"

    return {
        "session_id": session_id,
        "batches_requested": max_batches,
        "batches_completed": batches_completed,
        "items_scored": items_scored,
        "unscored_remaining": unscored_remaining,
        "m3_status": m3_status,
        "m3_error": m3_error if m3_status == "unavailable" else last_error,
        "model_call_ids": model_call_ids,
        "snapshot": snapshot,
    }


def schedule_m3_scoring_background(
    *,
    db_path: str,
    session_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
) -> None:
    """Run request_m3_scoring_batches on a daemon thread (API BackgroundTasks)."""
    from pathlib import Path

    from .db import connect, init_db

    if max_batches < 1:
        return


    key = _background_session_key(db_path, session_id)
    with _BACKGROUND_LOCK:
        if key in _RUNNING_BACKGROUND_KEYS:
            return
        _RUNNING_BACKGROUND_KEYS.add(key)
        _RUNNING_BACKGROUND_SESSIONS.add(session_id)

    def _run() -> None:
        conn = connect(Path(db_path))
        init_db(conn)
        try:
            request_m3_scoring_batches(
                conn,
                session_id,
                batch_size=batch_size,
                max_batches=max_batches,
            )
        finally:
            conn.close()
            with _BACKGROUND_LOCK:
                _RUNNING_BACKGROUND_KEYS.discard(key)
                _RUNNING_BACKGROUND_SESSIONS.discard(session_id)

    threading.Thread(target=_run, daemon=True).start()
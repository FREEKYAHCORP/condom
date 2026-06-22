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

ModelCall = Callable[[str], ModelCallResult]

_BACKGROUND_LOCK = threading.Lock()
_RUNNING_BACKGROUND_KEYS: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


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
) -> list[dict[str, Any]]:
    """Joined candidate_window_items + items without an ambient_m3_item_scores row."""
    if not _table_exists(conn, "ambient_m3_item_scores"):
        joined = load_joined_candidate_rows(conn, session_id)
        if limit is not None:
            return joined[:limit]
        return joined

    sql = """
        SELECT cwi.session_id, cwi.item_id, cwi.window_rank, cwi.first_response_id,
               cwi.first_captured_at, cwi.source,
               i.source AS item_source, i.batch_id, i.original_rank,
               i.author_handle, i.author_name, i.author_bio, i.text, i.quoted_text,
               i.thread_context, i.media_desc, i.link_url, i.link_title, i.link_excerpt,
               i.engagement, i.rendered_text, i.raw_json, i.harvested_at
        FROM candidate_window_items AS cwi
        JOIN items AS i ON i.item_id = cwi.item_id
        LEFT JOIN ambient_m3_item_scores AS s
          ON s.session_id = cwi.session_id AND s.item_id = cwi.item_id
        WHERE cwi.session_id = ? AND s.item_id IS NULL
        ORDER BY cwi.window_rank ASC
    """
    params: list[Any] = [session_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


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


def load_feed_status(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    candidate_count = count_candidate_items(conn, session_id)
    scored_count = count_scored_items(conn, session_id)
    unscored_count = max(0, candidate_count - scored_count)
    base_status, m3_error = _m3_availability()
    if unscored_count <= 0:
        m3_status = "idle" if base_status != "unavailable" else "unavailable"
    else:
        m3_status = base_status
    return {
        "session_id": session_id,
        "candidate_count": candidate_count,
        "scored_count": scored_count,
        "unscored_count": unscored_count,
        "latest_graphql_at": _latest_graphql_at(conn, session_id),
        "latest_m3_score_at": _latest_m3_score_at(conn, session_id),
        "latest_feed_snapshot_at": _latest_feed_snapshot_at(conn, session_id),
        "m3_queue_depth": unscored_count,
        "m3_status": m3_status,
        "m3_error": m3_error,
    }


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
) -> dict[str, Any]:
    """Order: M3 score desc, then native window_rank asc. Unscored items sort after scored."""
    joined = load_joined_candidate_rows(conn, session_id)
    scores = load_ambient_score_map(conn, session_id)
    ordered = sorted(joined, key=lambda r: _sort_key_for_snapshot(r, scores))
    if limit is not None:
        ordered = ordered[:limit]

    items: list[dict[str, Any]] = []
    for rank, row in enumerate(ordered, start=1):
        item_id = str(row["item_id"])
        entry = scores.get(item_id)
        items.append(
            {
                "item_id": item_id,
                "rank": rank,
                "snapshot_rank": rank,
                "window_rank": int(row.get("window_rank") or 0),
                "score": None if entry is None else entry["score"],
                "m3_score": None if entry is None else entry["score"],
                "tier": None if entry is None else entry["tier"],
                "serve": None if entry is None else entry["serve"],
                "reason": None if entry is None else entry["reason"],
            }
        )

    snapshot_id = f"{kind}:{session_id}:{datetime.now(timezone.utc).timestamp()}:{uuid.uuid4().hex}"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "snapshot_id": snapshot_id,
        "kind": kind,
        "arm": AMBIENT_M3_ARM,
        "effective_arm": AMBIENT_M3_ARM,
        "created_at": _utc_now(),
        "candidate_count": len(joined),
        "ordered_item_ids": [item["item_id"] for item in items],
        "items": items,
    }
    if persist:
        persist_feed_snapshot(conn, snapshot_id, session_id, kind, len(joined), items)
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


def get_feed_current(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Latest persisted immediate snapshot, or a fresh native-order snapshot (persisted). Never calls M3."""
    header = _load_latest_snapshot_header(conn, session_id)
    if header is None:
        return build_immediate_feed_snapshot(conn, session_id, limit=limit, persist=True)
    current_candidate_count = count_candidate_items(conn, session_id)
    latest_score_at = _latest_m3_score_at(conn, session_id)
    if int(header["candidate_count"]) != current_candidate_count:
        return build_immediate_feed_snapshot(conn, session_id, limit=limit, persist=True)
    if latest_score_at is not None and str(latest_score_at) > str(header["created_at"]):
        return build_immediate_feed_snapshot(conn, session_id, limit=limit, persist=True)

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
    items = []
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
    return {
        "session_id": session_id,
        "snapshot_id": snapshot_id,
        "kind": header["kind"],
        "arm": AMBIENT_M3_ARM,
        "effective_arm": AMBIENT_M3_ARM,
        "created_at": header["created_at"],
        "candidate_count": int(header["candidate_count"]),
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
    batch_size: int = 50,
    max_batches: int = 1,
    model_call: ModelCall | None = None,
    rebuild_snapshot: bool = True,
) -> dict[str, Any]:
    """Sync: score up to max_batches unscored rows. Default model_call is call_minimax when invoked."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_batches < 1:
        raise ValueError("max_batches must be >= 1")

    if model_call is None:
        m3_status, m3_error = _m3_availability()
        if m3_status == "unavailable":
            unscored = max(0, count_candidate_items(conn, session_id) - count_scored_items(conn, session_id))
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

    for _ in range(max_batches):
        batch = load_unscored_candidate_rows(conn, session_id, limit=batch_size)
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
        items_scored += int(result.get("items_scored") or 0)
        if not result.get("parsed_ok") and int(result.get("items_scored") or 0) <= 0:
            break

    unscored_remaining = max(0, count_candidate_items(conn, session_id) - count_scored_items(conn, session_id))
    if unscored_remaining == 0 and m3_status not in ("error", "unavailable"):
        m3_status = "idle"
    elif unscored_remaining > 0 and m3_status not in ("error", "unavailable"):
        m3_status = "ready"

    snapshot = None
    if rebuild_snapshot and items_scored > 0:
        snapshot = build_immediate_feed_snapshot(conn, session_id, persist=True)

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
    batch_size: int = 50,
    max_batches: int = 1,
) -> None:
    """Run request_m3_scoring_batches on a daemon thread (API BackgroundTasks)."""
    from pathlib import Path

    from .db import connect, init_db

    if max_batches < 1:
        return

    key = f"{db_path}:{session_id}"
    with _BACKGROUND_LOCK:
        if key in _RUNNING_BACKGROUND_KEYS:
            return
        _RUNNING_BACKGROUND_KEYS.add(key)

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

    threading.Thread(target=_run, daemon=True).start()
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DB_PATH, ensure_dirs
from .models import Item
from .render_timeline import render_timeline


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  item_id        TEXT PRIMARY KEY,
  source         TEXT NOT NULL,
  session_id     TEXT NOT NULL,
  batch_id       TEXT,
  original_rank  INTEGER,
  author_handle  TEXT,
  author_name    TEXT,
  author_bio     TEXT,
  text           TEXT,
  quoted_text    TEXT,
  thread_context TEXT,
  media_desc     TEXT,
  link_url       TEXT,
  link_title     TEXT,
  link_excerpt   TEXT,
  engagement     TEXT,
  rendered_text  TEXT NOT NULL,
  raw_json       TEXT NOT NULL,
  harvested_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  event_id        TEXT PRIMARY KEY,
  item_id         TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  exposed         INTEGER NOT NULL,
  visible_ms      INTEGER,
  stop            INTEGER,
  save            INTEGER,
  look_sec        REAL,
  profile_open    INTEGER,
  thread_open     INTEGER,
  link_click      INTEGER,
  lens_feedback   TEXT,
  exposed_surface TEXT NOT NULL,
  ts              TEXT NOT NULL,
  FOREIGN KEY(item_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS identity_versions (
  version        INTEGER PRIMARY KEY,
  revealed       TEXT NOT NULL,
  endorsed       TEXT NOT NULL,
  never_serve    TEXT NOT NULL,
  changelog      TEXT NOT NULL,
  promoted       INTEGER NOT NULL DEFAULT 0,
  harness_score  REAL,
  token_count    INTEGER NOT NULL,
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_network_responses (
  response_id    TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL,
  url            TEXT NOT NULL,
  body           TEXT NOT NULL,
  parsed_ok      INTEGER NOT NULL,
  parsed_count   INTEGER NOT NULL,
  error          TEXT,
  captured_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arm_predictions (
  prediction_id   TEXT PRIMARY KEY,
  arm             TEXT NOT NULL,
  item_id         TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  batch_id        TEXT NOT NULL,
  rank            INTEGER,
  score           REAL,
  pred_stop       INTEGER,
  pred_open       INTEGER,
  pred_save       INTEGER,
  pred_look_sec   REAL,
  reaction_text   TEXT,
  receipt         TEXT,
  identity_version INTEGER NOT NULL DEFAULT 0,
  prompt_version  TEXT,
  rendered_text   TEXT,
  model_name      TEXT,
  created_at      TEXT NOT NULL,
  FOREIGN KEY(item_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS model_calls (
  call_id          TEXT PRIMARY KEY,
  arm              TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  batch_id         TEXT NOT NULL,
  model_name       TEXT,
  prompt_version   TEXT,
  request_json     TEXT NOT NULL,
  response_text    TEXT,
  parsed_ok        INTEGER NOT NULL,
  error            TEXT,
  latency_ms       INTEGER,
  input_tokens     INTEGER,
  output_tokens    INTEGER,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
  score_id          TEXT PRIMARY KEY,
  arm               TEXT NOT NULL,
  session_id        TEXT,
  split_name        TEXT NOT NULL,
  n_exposed         INTEGER NOT NULL,
  n_saves           INTEGER NOT NULL,
  headline_valid    INTEGER NOT NULL DEFAULT 1,
  save_precision    REAL NOT NULL,
  save_recall       REAL NOT NULL,
  save_fbeta        REAL NOT NULL,
  save_precision_at_12 REAL,
  save_recall_at_12    REAL,
  utility_ndcg_at_12   REAL,
  false_skip_saves  INTEGER NOT NULL,
  false_pull_count  INTEGER NOT NULL,
  stop_accuracy     REAL,
  open_auc          REAL,
  look_mae_bucketed REAL,
  created_at        TEXT NOT NULL
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Lightweight migrations for existing experiment.sqlite files.
    event_cols = _columns(conn, "events")
    if "lens_feedback" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN lens_feedback TEXT")
    pred_cols = _columns(conn, "arm_predictions")
    if "identity_version" not in pred_cols:
        conn.execute("ALTER TABLE arm_predictions ADD COLUMN identity_version INTEGER NOT NULL DEFAULT 0")
    score_cols = _columns(conn, "scores")
    if "headline_valid" not in score_cols:
        conn.execute("ALTER TABLE scores ADD COLUMN headline_valid INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def upsert_items(conn: sqlite3.Connection, items: Iterable[Item], harvested_at: str) -> int:
    count = 0
    for item in items:
        conn.execute(
            """
            INSERT INTO items (
              item_id, source, session_id, batch_id, original_rank, author_handle,
              author_name, author_bio, text, quoted_text, thread_context, media_desc,
              link_url, link_title, link_excerpt, engagement, rendered_text, raw_json,
              harvested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
              session_id=excluded.session_id,
              batch_id=COALESCE(items.batch_id, excluded.batch_id),
              original_rank=COALESCE(items.original_rank, excluded.original_rank),
              author_handle=COALESCE(items.author_handle, excluded.author_handle),
              author_name=COALESCE(items.author_name, excluded.author_name),
              text=COALESCE(items.text, excluded.text),
              quoted_text=COALESCE(items.quoted_text, excluded.quoted_text),
              media_desc=COALESCE(items.media_desc, excluded.media_desc),
              link_url=COALESCE(items.link_url, excluded.link_url),
              link_title=COALESCE(items.link_title, excluded.link_title),
              link_excerpt=COALESCE(items.link_excerpt, excluded.link_excerpt),
              rendered_text=excluded.rendered_text,
              raw_json=excluded.raw_json
            """,
            (
                item.item_id,
                item.source,
                item.session_id,
                item.batch_id,
                item.original_rank,
                item.author_handle,
                item.author_name,
                item.author_bio,
                item.text,
                item.quoted_text,
                item.thread_context,
                item.media_desc,
                item.link_url,
                item.link_title,
                item.link_excerpt,
                json.dumps(item.engagement or {}, ensure_ascii=True),
                render_timeline(item),
                json.dumps(item.raw_json or {}, ensure_ascii=True),
                harvested_at,
            ),
        )
        count += 1
    conn.commit()
    return count


def insert_events(conn: sqlite3.Connection, events: Iterable[dict[str, Any]]) -> int:
    count = 0
    for ev in events:
        conn.execute(
            """
            INSERT OR REPLACE INTO events (
              event_id, item_id, session_id, exposed, visible_ms, stop, save,
              look_sec, profile_open, thread_open, link_click, lens_feedback, exposed_surface, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev["event_id"],
                ev["item_id"],
                ev["session_id"],
                int(ev.get("exposed", 1)),
                ev.get("visible_ms"),
                int(ev.get("stop", 0)),
                int(ev.get("save", 0)),
                ev.get("look_sec", 0.0),
                int(ev.get("profile_open", 0)),
                int(ev.get("thread_open", 0)),
                int(ev.get("link_click", 0)),
                ev.get("lens_feedback"),
                ev.get("exposed_surface", "x_for_you"),
                ev["ts"],
            ),
        )
        count += 1
    conn.commit()
    return count


def insert_raw_network_response(
    conn: sqlite3.Connection,
    *,
    response_id: str,
    session_id: str,
    url: str,
    body: str,
    parsed_ok: bool,
    parsed_count: int,
    error: str | None = None,
    captured_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO raw_network_responses (
          response_id, session_id, url, body, parsed_ok, parsed_count, error, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            response_id,
            session_id,
            url,
            body,
            1 if parsed_ok else 0,
            parsed_count,
            error,
            captured_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def clear_arm(conn: sqlite3.Connection, arm: str, session_id: str) -> None:
    conn.execute("DELETE FROM arm_predictions WHERE arm=? AND session_id=?", (arm, session_id))
    conn.execute("DELETE FROM scores WHERE arm=? AND session_id=?", (arm, session_id))
    conn.commit()

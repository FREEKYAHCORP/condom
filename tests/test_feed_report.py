from __future__ import annotations

import html
import json
import sqlite3

from condom_core.db import init_db, insert_feed_run, upsert_items
from condom_core.feed_generation import M3_FEED_ARM
from condom_core.feed_report import (
    arm_display_label,
    feed_run_ratio_card_html,
    load_latest_feed_runs,
    render_feed_run_ratio_cards,
)
from condom_core.models import Item
from condom_core.rankers.cheap_combo import ARM as CHEAP_COMBO_ARM
from condom_core.rankers.native import ARM as NATIVE_ARM


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _html_escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _seed_item(conn: sqlite3.Connection, session_id: str, item_id: str) -> None:
    upsert_items(
        conn,
        [
            Item(
                item_id=item_id,
                source="x",
                session_id=session_id,
                author_handle="h",
                text="t",
            )
        ],
        "2026-06-21T12:00:00Z",
    )


def test_load_latest_feed_runs_picks_newest_per_arm():
    conn = _memory_conn()
    session_id = "sess-runs"
    _seed_item(conn, session_id, "2066000000000000001")

    insert_feed_run(
        conn,
        feed_run_id="native-old",
        arm=NATIVE_ARM,
        session_id=session_id,
        candidate_count=10,
        scored_count=10,
        curated_count=5,
        coverage_ratio=1.0,
        curation_ratio=0.5,
        curated_k=5,
        metrics={},
        selected_item_ids=["2066000000000000001"],
    )
    conn.execute(
        "UPDATE feed_runs SET created_at = ? WHERE feed_run_id = ?",
        ("2026-06-21T10:00:00Z", "native-old"),
    )
    insert_feed_run(
        conn,
        feed_run_id="native-new",
        arm=NATIVE_ARM,
        session_id=session_id,
        candidate_count=20,
        scored_count=20,
        curated_count=12,
        coverage_ratio=1.0,
        curation_ratio=0.6,
        curated_k=12,
        metrics={},
        selected_item_ids=["2066000000000000001"],
    )
    conn.execute(
        "UPDATE feed_runs SET created_at = ? WHERE feed_run_id = ?",
        ("2026-06-21T11:00:00Z", "native-new"),
    )
    conn.commit()

    rows = load_latest_feed_runs(conn, session_id)
    native_rows = [r for r in rows if r["arm"] == NATIVE_ARM]
    assert len(native_rows) == 1
    assert native_rows[0]["feed_run_id"] == "native-new"
    assert native_rows[0]["candidate_count"] == 20


def test_feed_run_ratio_card_html_includes_coverage_and_curation():
    row = {
        "arm": CHEAP_COMBO_ARM,
        "candidate_count": 36,
        "scored_count": 36,
        "curated_count": 12,
        "curated_k": 12,
        "coverage_ratio": 1.0,
        "curation_ratio": 0.333,
        "prefilter_count": None,
        "prefilter_ratio": None,
        "metrics_json": None,
    }
    card = feed_run_ratio_card_html(row, escape=_html_escape)
    assert "Cheap Combo" in card
    assert "coverage 1.000" in card
    assert "curation 0.333" in card
    assert "x_returned_candidates" in card
    assert "cheap_combo_v0" in card


def test_feed_run_ratio_card_html_prefilter_line_when_present():
    row = {
        "arm": M3_FEED_ARM,
        "candidate_count": 50,
        "scored_count": 36,
        "curated_count": 12,
        "curated_k": 12,
        "coverage_ratio": 0.72,
        "curation_ratio": 0.333,
        "prefilter_count": 36,
        "prefilter_ratio": 0.72,
        "metrics_json": json.dumps({"selected_feed": "balanced"}),
    }
    card = feed_run_ratio_card_html(row, escape=_html_escape)
    assert "prefilter 36" in card
    assert "0.720" in card


def test_render_feed_run_ratio_cards_empty_and_populated():
    empty = render_feed_run_ratio_cards([], escape=_html_escape)
    assert "No feed_runs rows" in empty

    row = {
        "arm": NATIVE_ARM,
        "candidate_count": 8,
        "scored_count": 8,
        "curated_count": 4,
        "curated_k": 4,
        "coverage_ratio": 1.0,
        "curation_ratio": 0.5,
        "prefilter_count": None,
        "prefilter_ratio": None,
        "metrics_json": None,
    }
    block = render_feed_run_ratio_cards([row], escape=_html_escape)
    assert "feed-run-metrics" in block
    assert arm_display_label(NATIVE_ARM) in block
    assert "coverage 1.000" in block
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from condom_core.db import init_db, insert_feed_run, upsert_items
from condom_core.feed_generation import M3_FEED_ARM
from condom_core.feed_judge import (
    DEFAULT_JUDGE_ARMS,
    build_blind_judge_packet,
    check_packet_leakage,
    insert_feed_evaluation,
    load_feed_evaluation,
    load_feed_run_sanitized_items,
    render_blind_feed_text,
    store_judge_result_json,
)
from condom_core.models import Item
from condom_core.rankers.cheap_combo import ARM as CHEAP_COMBO_ARM
from condom_core.rankers.native import ARM as NATIVE_ARM


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _item(
    *,
    item_id: str,
    session_id: str,
    handle: str,
    text: str,
) -> Item:
    return Item(
        item_id=item_id,
        source="x",
        session_id=session_id,
        author_handle=handle,
        text=text,
    )


def _seed_feed_run(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    arm: str,
    feed_run_id: str,
    item_specs: list[tuple[str, str, str]],
    created_at: str,
    model_name: str | None = None,
) -> None:
    """item_specs: (item_id, handle, text) in feed order."""
    upsert_items(
        conn,
        [_item(item_id=i, session_id=session_id, handle=h, text=t) for i, h, t in item_specs],
        created_at,
    )
    n = len(item_specs)
    insert_feed_run(
        conn,
        feed_run_id=feed_run_id,
        arm=arm,
        session_id=session_id,
        candidate_count=n,
        scored_count=n,
        curated_count=n,
        coverage_ratio=1.0,
        curation_ratio=1.0,
        curated_k=n,
        metrics={},
        selected_item_ids=[i for i, _, _ in item_specs],
        model_call_id=None,
        prompt_version=None,
        model_name=model_name,
        prefilter_count=None,
        prefilter_ratio=None,
    )
    conn.execute(
        "UPDATE feed_runs SET created_at = ? WHERE feed_run_id = ?",
        (created_at, feed_run_id),
    )
    conn.commit()


def _seed_three_judge_arms(conn: sqlite3.Connection, session_id: str) -> None:
    specs = {
        NATIVE_ARM: ("run-native", [("2066000000000000001", "native_author", "native arm tweet")]),
        CHEAP_COMBO_ARM: (
            "run-cheap",
            [("2066000000000000002", "cheap_author", "cheap combo tweet")],
        ),
        M3_FEED_ARM: (
            "run-m3",
            [("2066000000000000003", "m3_author", "m3 selection tweet")],
        ),
    }
    for idx, arm in enumerate(DEFAULT_JUDGE_ARMS):
        run_id, items = specs[arm]
        _seed_feed_run(
            conn,
            session_id=session_id,
            arm=arm,
            feed_run_id=run_id,
            item_specs=items,
            created_at=f"2026-06-21T12:00:{idx:02d}Z",
            model_name="MiniMax-M2.5" if arm == M3_FEED_ARM else None,
        )


def test_render_blind_feed_text_omits_item_ids_and_uses_position():
    body = render_blind_feed_text(
        [
            {"author": "alice", "text": "first post"},
            {"author": "bob", "text": "second post"},
        ]
    )
    assert "1. @alice" in body
    assert "first post" in body
    assert "2. @bob" in body
    assert "2066" not in body
    assert "item_id" not in body.lower()


def test_check_packet_leakage_flags_arm_tokens_and_long_ids():
    bad = "Feed mentions native_x_order and id 2066000000000000001"
    result = check_packet_leakage(bad, secret_tokens=["run-secret"])
    assert result["ok"] is False
    assert any("native_x_order" in h for h in result["hits"])
    assert any("long_numeric" in h for h in result["hits"])


def test_check_packet_leakage_allows_x_returned_candidates_wording():
    ok_text = (
        "Each feed used the same x_returned_candidates candidate window. "
        "Judge fit only.\n1. @reader\nhello"
    )
    result = check_packet_leakage(ok_text)
    assert result["ok"] is True


def test_load_feed_run_sanitized_items_strips_handles_and_collapses_whitespace():
    conn = _memory_conn()
    session_id = "sess-sanitize"
    _seed_feed_run(
        conn,
        session_id=session_id,
        arm=NATIVE_ARM,
        feed_run_id="run-one",
        item_specs=[("2066000000000000099", "@stripme", "line   one\n\ttwo")],
        created_at="2026-06-21T12:00:00Z",
    )
    rows = load_feed_run_sanitized_items(conn, feed_run_id="run-one")
    assert len(rows) == 1
    assert rows[0]["author"] == "stripme"
    assert rows[0]["text"] == "line one two"
    assert "item_id" not in rows[0]


def test_build_blind_judge_packet_splits_packet_key_meta(tmp_path: Path):
    conn = _memory_conn()
    session_id = "sess-judge"
    _seed_three_judge_arms(conn, session_id)

    out = build_blind_judge_packet(
        conn,
        session_id=session_id,
        seed=42,
        output_dir=tmp_path,
        limit=5,
    )

    packet_text = out["packet_path"].read_text(encoding="utf-8")
    key_payload = json.loads(out["key_path"].read_text(encoding="utf-8"))
    meta_payload = json.loads(out["meta_path"].read_text(encoding="utf-8"))

    assert out["leakage"]["ok"] is True
    assert "x_returned_candidates" in packet_text
    assert "native_x_order" not in packet_text.lower()
    assert "cheap_combo_v0" not in packet_text.lower()
    assert "m3_feed_selection_v0" not in packet_text.lower()
    for item_id in (
        "2066000000000000001",
        "2066000000000000002",
        "2066000000000000003",
    ):
        assert item_id not in packet_text

    assert set(key_payload["labels"]) == {"A", "B", "C"}
    assert set(key_payload["labels"].values()) == set(DEFAULT_JUDGE_ARMS)
    assert key_payload["seed"] == 42
    assert "feed_run_ids" in key_payload

    assert meta_payload["label_to_arm"] == out["label_to_arm"]
    assert meta_payload["feed_run_ids"] == out["feed_run_ids"]
    assert meta_payload["leakage"]["ok"] is True

    leakage_with_secrets = check_packet_leakage(
        packet_text,
        secret_tokens=[NATIVE_ARM, "run-native", "MiniMax-M2.5"],
    )
    assert leakage_with_secrets["ok"] is True


def test_build_blind_judge_packet_label_shuffle_is_seed_deterministic(tmp_path: Path):
    conn = _memory_conn()
    session_id = "sess-seed"
    _seed_three_judge_arms(conn, session_id)

    a = build_blind_judge_packet(
        conn, session_id=session_id, seed=7, output_dir=tmp_path / "a"
    )
    b = build_blind_judge_packet(
        conn, session_id=session_id, seed=7, output_dir=tmp_path / "b"
    )
    c = build_blind_judge_packet(
        conn, session_id=session_id, seed=99, output_dir=tmp_path / "c"
    )

    assert a["label_to_arm"] == b["label_to_arm"]
    assert a["label_to_arm"] != c["label_to_arm"]


def test_feed_evaluation_round_trip_and_judge_result_json():
    conn = _memory_conn()
    evaluation_id = "eval-test-1"
    judge_blob = {"feed_scores": {"A": 8, "B": 6, "C": 7}, "final_ranking": ["A", "C", "B"]}

    insert_feed_evaluation(
        conn,
        evaluation_id=evaluation_id,
        session_id="sess-eval",
        packet_path="/tmp/packet.md",
        key_path="/tmp/key.json",
        meta_path="/tmp/meta.json",
        prompt_version="qualitative_feed_judge_v0.md",
        label_map_json=json.dumps({"A": NATIVE_ARM}),
        leakage_ok=True,
        leakage_json=json.dumps({"ok": True, "hits": []}),
        judge_result_json=None,
        created_at="2026-06-21T12:00:00Z",
    )
    row = load_feed_evaluation(conn, evaluation_id)
    assert row is not None
    assert row["leakage_ok"] == 1
    assert row["judge_result_json"] is None

    store_judge_result_json(conn, evaluation_id=evaluation_id, judge_result=judge_blob)
    row2 = load_feed_evaluation(conn, evaluation_id)
    assert row2 is not None
    assert json.loads(row2["judge_result_json"]) == judge_blob


def test_build_blind_judge_packet_raises_when_arm_missing(tmp_path: Path):
    conn = _memory_conn()
    session_id = "sess-partial"
    _seed_feed_run(
        conn,
        session_id=session_id,
        arm=NATIVE_ARM,
        feed_run_id="only-native",
        item_specs=[("2066000000000000001", "a", "t")],
        created_at="2026-06-21T12:00:00Z",
    )
    with pytest.raises(ValueError, match="Missing latest feed_runs"):
        build_blind_judge_packet(conn, session_id=session_id, output_dir=tmp_path)

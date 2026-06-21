from __future__ import annotations

import json
import sqlite3

import pytest

from condom_core.candidate_window import insert_x_returned_candidates, rebuild_candidate_window
from condom_core.db import init_db, insert_raw_network_response, upsert_items
from condom_core.feed_generation import (
    M3_FEED_ARM,
    build_cheap_feed,
    load_joined_candidate_rows,
    run_cheap_feed,
    run_m3_feed_selection,
    run_native_feed,
)
from condom_core.parse_llm_outputs import parse_feed_selection_json
from condom_core.parse_x import parse_response_ordered
from condom_core.prompts import FEED_SELECTION_PROMPT_VERSION
from condom_core.rankers.cheap_combo import ARM as CHEAP_COMBO_ARM, rank_combo
from condom_core.rankers.native import ARM as NATIVE_ARM


def _timeline_entries(*tweets: dict) -> dict:
    return {
        "data": {
            "home": {
                "home_timeline_urt": {
                    "instructions": [
                        {
                            "entries": [
                                {
                                    "content": {
                                        "itemContent": {
                                            "tweet_results": {"result": tweet},
                                        }
                                    }
                                }
                                for tweet in tweets
                            ]
                        }
                    ]
                }
            }
        }
    }


def _tweet(*, rest_id: str, text: str, handle: str = "author") -> dict:
    user = {
        "core": {"screen_name": handle, "name": handle.title()},
        "legacy": {"screen_name": handle, "name": handle.title(), "description": None},
    }
    return {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "legacy": {
            "id_str": rest_id,
            "full_text": text,
            "favorite_count": 1,
            "retweet_count": 0,
            "reply_count": 0,
            "quote_count": 0,
        },
        "core": {"user_results": {"result": user}},
    }


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_candidate_window(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    tweets: list[tuple[str, str]],
) -> list[str]:
    """Insert ordered x_returned_candidates window; returns item ids in window order."""
    body = _timeline_entries(*[_tweet(rest_id=i, text=t) for i, t in tweets])
    source = "x_for_you"
    captured_at = "2026-06-21T00:00:00Z"
    ordered = parse_response_ordered(body, session_id=session_id, source=source)
    items = [item for item, _ in ordered]
    insert_raw_network_response(
        conn,
        response_id="resp-feed-1",
        session_id=session_id,
        url="https://x.com/i/api/graphql/HomeTimeline",
        body=json.dumps(body, ensure_ascii=True),
        parsed_ok=True,
        parsed_count=len(items),
        error=None,
        captured_at=captured_at,
    )
    upsert_items(conn, items, captured_at)
    insert_x_returned_candidates(
        conn,
        session_id=session_id,
        response_id="resp-feed-1",
        ordered=ordered,
        source=source,
        captured_at=captured_at,
    )
    rebuild_candidate_window(conn, session_id, source=source)
    return [item.item_id for item, _ in ordered]


def _valid_m3_json(*, selected_ids: list[str], feed: str = "balanced") -> str:
    behavior = {
        item_id: {
            "stop": True,
            "open": False,
            "save": False,
            "look_sec": 2,
            "why": "I'd skim this.",
        }
        for item_id in selected_ids
    }
    payload = {
        "selected_feed": feed,
        "selected_item_ids": selected_ids,
        "feed_scores": {"precision": 7, "exploration": 6, "balanced": 8},
        "curation_ratio_note": "Hit target count.",
        "predicted_behavior": behavior,
        "dropped_item_ids": [],
        "short_verdict": "Balanced slate fits me right now.",
    }
    return json.dumps(payload)


def test_parse_feed_selection_json_rejects_invented_id():
    candidate_ids = {"1001", "1002", "1003"}
    bad = _valid_m3_json(selected_ids=["1001", "9999"])
    with pytest.raises(ValueError, match="invented selected id"):
        parse_feed_selection_json(bad, candidate_ids, target_n=2)


def test_parse_feed_selection_json_ignores_invented_dropped_ids():
    candidate_ids = {"1001", "1002", "1003"}
    payload = json.loads(_valid_m3_json(selected_ids=["1001", "1002"]))
    payload["dropped_item_ids"] = ["1003", "9999"]

    parsed = parse_feed_selection_json(json.dumps(payload), candidate_ids, target_n=2)

    assert parsed["dropped_item_ids"] == ["1003"]



def test_parse_feed_selection_json_repairs_near_behavior_id_typo():
    candidate_ids = {"2068150098694111673", "2068319367738151093"}
    payload = json.loads(_valid_m3_json(selected_ids=["2068150098694111673"]))
    payload["predicted_behavior"]["2068150098694011673"] = payload["predicted_behavior"].pop("2068150098694111673")

    parsed = parse_feed_selection_json(json.dumps(payload), candidate_ids, target_n=1)

    assert "2068150098694111673" in parsed["predicted_behavior"]

def test_native_feed_metrics_use_full_candidate_window_as_scored():
    conn = _memory_conn()
    session_id = "sess-native-metrics"
    ids = _seed_candidate_window(
        conn,
        session_id,
        tweets=[
            ("7001", "first in window"),
            ("7002", "second"),
            ("7003", "third"),
            ("7004", "fourth"),
            ("7005", "fifth"),
        ],
    )
    curated_k = 3
    result = run_native_feed(conn, session_id, curated_k=curated_k, refresh=True)
    metrics = result["metrics"]

    assert metrics["candidate_count"] == 5
    assert metrics["scored_count"] == 5
    assert metrics["curated_count"] == curated_k
    assert metrics["coverage_ratio"] == pytest.approx(1.0)
    assert metrics["curation_ratio"] == pytest.approx(curated_k / 5)
    assert metrics["prefilter_count"] is None
    assert metrics["prefilter_ratio"] is None
    assert result["selected_item_ids"] == ids[:curated_k]

    row = conn.execute(
        "SELECT * FROM feed_runs WHERE arm = ? AND session_id = ?",
        (NATIVE_ARM, session_id),
    ).fetchone()
    assert row is not None
    assert row["candidate_count"] == 5
    assert row["scored_count"] == 5
    assert row["coverage_ratio"] == pytest.approx(1.0)


def test_cheap_feed_scores_all_candidates_but_curates_target_k():
    conn = _memory_conn()
    session_id = "sess-cheap-metrics"
    _seed_candidate_window(
        conn,
        session_id,
        tweets=[
            ("8001", "cat pictures and memes"),
            ("8002", "benchmarks and evaluations for ML agents"),
            ("8003", "random lunch update"),
            ("8004", "open source AI infrastructure benchmarks"),
            ("8005", "weather today"),
        ],
    )
    profile = "machine learning\nbenchmarks\nevaluations\nagents\n"
    curated_k = 2
    result = run_cheap_feed(
        conn,
        session_id,
        curated_k=curated_k,
        refresh=True,
        positive_profile_text=profile,
    )
    metrics = result["metrics"]

    assert metrics["candidate_count"] == 5
    assert metrics["scored_count"] == 5
    assert metrics["curated_count"] == curated_k
    assert metrics["coverage_ratio"] == pytest.approx(1.0)
    assert metrics["curation_ratio"] == pytest.approx(curated_k / 5)


    rows = load_joined_candidate_rows(conn, session_id)
    expected_top = build_cheap_feed(rows, curated_k, positive_profile_text=profile)
    assert result["selected_item_ids"] == expected_top
    assert len(result["selected_item_ids"]) == curated_k

    # rank_combo touched every candidate (full-window score sort)
    full_ranked = [row["item_id"] for row, _ in rank_combo(rows, profile)]
    assert len(full_ranked) == 5
    assert set(full_ranked) == {r["item_id"] for r in rows}

    pred_count = conn.execute(
        "SELECT COUNT(*) AS n FROM arm_predictions WHERE arm = ? AND session_id = ?",
        (CHEAP_COMBO_ARM, session_id),
    ).fetchone()["n"]
    assert pred_count == curated_k


def test_m3_feed_selection_rejects_invented_ids_from_model():
    conn = _memory_conn()
    session_id = "sess-m3-bad"
    _seed_candidate_window(
        conn,
        session_id,
        tweets=[
            ("9001", "one"),
            ("9002", "two"),
            ("9003", "three"),
        ],
    )

    def bad_model(_prompt: str) -> str:
        return _valid_m3_json(selected_ids=["9001", "invented-9009"])

    with pytest.raises(ValueError, match="invented selected id"):
        run_m3_feed_selection(conn, session_id, bad_model, curated_k=2, refresh=True)

def test_m3_feed_selection_retries_on_parse_error_then_succeeds():
    conn = _memory_conn()
    session_id = "sess-m3-retry"
    _seed_candidate_window(
        conn,
        session_id,
        tweets=[
            ("9201", "one"),
            ("9202", "two"),
            ("9203", "three"),
        ],
    )
    curated_k = 2
    calls: list[str] = []

    def flaky_model(prompt: str) -> str:
        calls.append(prompt)
        rows = load_joined_candidate_rows(conn, session_id)
        pick = [row["item_id"] for row in rows[:curated_k]]
        if len(calls) == 1:
            return "not valid json at all"
        return _valid_m3_json(selected_ids=pick)

    result = run_m3_feed_selection(
        conn,
        session_id,
        flaky_model,
        curated_k=curated_k,
        refresh=True,
    )

    assert len(calls) == 2
    assert "Your previous output failed JSON validation" in calls[1]
    assert len(result["selected_item_ids"]) == curated_k

    model_calls = conn.execute(
        "SELECT parsed_ok, error FROM model_calls WHERE arm = ? AND session_id = ? ORDER BY created_at",
        (M3_FEED_ARM, session_id),
    ).fetchall()
    assert len(model_calls) == 2
    assert model_calls[0]["parsed_ok"] == 0
    assert model_calls[1]["parsed_ok"] == 1

    feed_row = conn.execute(
        "SELECT model_call_id FROM feed_runs WHERE arm = ? AND session_id = ?",
        (M3_FEED_ARM, session_id),
    ).fetchone()
    assert feed_row is not None
    ok_call = conn.execute(
        "SELECT call_id FROM model_calls WHERE arm = ? AND session_id = ? AND parsed_ok = 1",
        (M3_FEED_ARM, session_id),
    ).fetchone()
    assert feed_row["model_call_id"] == ok_call["call_id"]

def test_m3_feed_selection_valid_writes_feed_run_and_predictions():
    conn = _memory_conn()
    session_id = "sess-m3-ok"
    _seed_candidate_window(
        conn,
        session_id,
        tweets=[
            ("9101", "benchmarks for agents"),
            ("9102", "meme thread"),
            ("9103", "evaluations paper"),
            ("9104", "coffee"),
            ("9105", "ML infrastructure"),
            ("9106", "sports"),
        ],
    )
    curated_k = 3
    captured: list[str] = []

    def good_model(prompt: str) -> str:
        captured.append(prompt)
        # Model picks ids from the candidate window (prefilter includes all when n<=36).

        rows = load_joined_candidate_rows(conn, session_id)
        pick = [row["item_id"] for row in rows[:curated_k]]
        return _valid_m3_json(selected_ids=pick, feed="precision")

    result = run_m3_feed_selection(
        conn,
        session_id,
        good_model,
        curated_k=curated_k,
        refresh=True,
        model_name="fake-m3",
    )
    metrics = result["metrics"]

    assert metrics["candidate_count"] == 6
    assert metrics["scored_count"] == metrics["prefilter_count"]
    assert metrics["prefilter_count"] is not None
    assert metrics["prefilter_ratio"] == pytest.approx(metrics["prefilter_count"] / 6)
    assert metrics["coverage_ratio"] == pytest.approx(metrics["scored_count"] / 6)
    assert metrics["curated_count"] == curated_k
    assert len(captured) == 1

    feed_row = conn.execute(
        "SELECT * FROM feed_runs WHERE arm = ? AND session_id = ?",
        (M3_FEED_ARM, session_id),
    ).fetchone()
    assert feed_row is not None
    assert feed_row["prompt_version"] == FEED_SELECTION_PROMPT_VERSION
    assert feed_row["model_name"] == "fake-m3"
    assert feed_row["prefilter_count"] == metrics["prefilter_count"]
    assert feed_row["prefilter_ratio"] == pytest.approx(metrics["prefilter_ratio"])

    call_row = conn.execute(
        "SELECT * FROM model_calls WHERE arm = ? AND session_id = ?",
        (M3_FEED_ARM, session_id),
    ).fetchone()
    assert call_row is not None
    assert call_row["parsed_ok"] == 1
    assert call_row["prompt_version"] == FEED_SELECTION_PROMPT_VERSION

    preds = conn.execute(
        """
        SELECT item_id, rank, prompt_version, model_name
        FROM arm_predictions
        WHERE arm = ? AND session_id = ?
        ORDER BY rank ASC
        """,
        (M3_FEED_ARM, session_id),
    ).fetchall()
    assert len(preds) == curated_k
    assert {p["item_id"] for p in preds} == set(result["selected_item_ids"])
    assert all(p["prompt_version"] == FEED_SELECTION_PROMPT_VERSION for p in preds)
    assert all(p["model_name"] == "fake-m3" for p in preds)

    items = conn.execute(
        "SELECT item_id, feed_rank FROM feed_run_items WHERE feed_run_id = ? ORDER BY feed_rank",
        (result["feed_run_id"],),
    ).fetchall()
    assert [row["item_id"] for row in items] == result["selected_item_ids"]
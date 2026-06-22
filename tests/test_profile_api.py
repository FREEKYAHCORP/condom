from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from condom_core import profile as profile_mod
from condom_core.profile import (
    DEFAULT_PROFILE_FIELDS,
    DEFAULT_STATE_PREAMBLE,
    PROFILE_FIELD_NAMES,
    ambient_m3_request_metadata,
    build_profile_prompt_preview,
    compile_ambient_m3_prompt_fields,
    get_active_profile_version,
    load_user_profile_store,
    reset_user_profile,
    save_profile_version,
)
from condom_core.prompts import build_ambient_m3_item_score_prompt


def _patch_profile_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(profile_mod, "PROFILE", tmp_path)


def _version_field_subset(version: dict) -> dict:
    return {k: version[k] for k in PROFILE_FIELD_NAMES if k in version}


def test_load_user_profile_store_creates_default_active_version(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    store = load_user_profile_store()
    assert store["active_version_id"]
    assert len(store["versions"]) == 1
    active = get_active_profile_version(store)
    assert active["profile_version_id"] == store["active_version_id"]
    assert _version_field_subset(active) == DEFAULT_PROFILE_FIELDS
    assert active["source"] in {"bootstrap", "default"}


def test_save_profile_version_appends_and_activates(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    load_user_profile_store()
    first = save_profile_version(
        {"state_preamble": "late night skim", "identity_revealed": "ml researcher"},
        source="extension",
    )
    second = save_profile_version(
        {"negative_profile": "crypto giveaways"},
        source="extension",
    )
    store = load_user_profile_store(create_if_missing=False)
    assert len(store["versions"]) == 3
    assert store["active_version_id"] == second["profile_version_id"]
    active = get_active_profile_version(store)
    assert active["negative_profile"] == "crypto giveaways"
    assert active["state_preamble"] == "late night skim"
    assert active["identity_revealed"] == "ml researcher"
    assert first["profile_version_id"] != second["profile_version_id"]


def test_reset_user_profile_appends_default_active_version(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    save_profile_version(
        {
            "state_preamble": "custom",
            "identity_revealed": "custom self",
            "negative_profile": "junk",
        },
        source="user",
    )
    reset = reset_user_profile(source="reset")
    store = load_user_profile_store(create_if_missing=False)
    assert store["active_version_id"] == reset["profile_version_id"]
    assert len(store["versions"]) == 3
    assert reset["source"] == "reset"
    assert _version_field_subset(reset) == DEFAULT_PROFILE_FIELDS
    assert reset["state_preamble"] == DEFAULT_STATE_PREAMBLE


def test_compile_ambient_m3_prompt_fields_uses_active_profile(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    save_profile_version(
        {
            "negative_profile": "PROFILE_NEG_MARKER_XYZ",
            "identity_revealed": "revealed marker",
        },
        source="test",
    )
    fields = compile_ambient_m3_prompt_fields()
    assert "PROFILE_NEG_MARKER_XYZ" in fields["negative_profile"]
    assert fields["identity_revealed"] == "revealed marker"


def test_build_profile_prompt_preview_includes_hash_and_negative_in_prompt(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    save_profile_version({"negative_profile": "suppress politics"}, source="test")
    preview = build_profile_prompt_preview()
    assert preview["profile_version_id"]
    assert preview["prompt_hash"].startswith("sha256:")
    assert "suppress politics" in preview["prompt"]
    assert "LOW-FIT" in preview["prompt"] or "SUPPRESS" in preview["prompt"]


def test_ambient_m3_request_metadata_includes_profile_version_id_and_prompt_hash():
    prompt = "sample prompt body"
    meta = ambient_m3_request_metadata(prompt, "pv_test123")
    assert meta["prompt"] == prompt
    assert meta["profile_version_id"] == "pv_test123"
    assert meta["prompt_hash"].startswith("sha256:")


def test_build_ambient_prompt_negative_profile_in_low_fit_section():
    prompt = build_ambient_m3_item_score_prompt(
        "<id1> sample",
        negative_profile="NEGATIVE_TOPIC_MARKER_ABC",
    )
    assert "LOW-FIT" in prompt or "SUPPRESS" in prompt
    assert "NEGATIVE_TOPIC_MARKER_ABC" in prompt


def test_profile_api_get_put_reset_and_prompt_preview(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    from condom_core.api.app import app

    client = TestClient(app)

    get0 = client.get("/profile")
    assert get0.status_code == 200
    body0 = get0.json()
    assert body0["ok"] is True
    assert body0["active_version_id"]
    assert body0["active"]["profile_version_id"] == body0["active_version_id"]
    initial_version_count = len(body0["versions"])

    put = client.put(
        "/profile",
        json={
            "state_preamble": "api edit",
            "negative_profile": "api suppress list",
            "source": "extension",
        },
    )
    assert put.status_code == 200
    saved = put.json()
    assert saved["ok"] is True
    assert saved["active"]["state_preamble"] == "api edit"
    assert saved["active"]["negative_profile"] == "api suppress list"
    assert saved["active"]["identity_revealed"] == DEFAULT_PROFILE_FIELDS["identity_revealed"]
    assert saved["version"]["profile_version_id"] == saved["active_version_id"]

    get1 = client.get("/profile")
    assert get1.status_code == 200
    after_put = get1.json()
    assert len(after_put["versions"]) == initial_version_count + 1

    reset_resp = client.post("/profile/reset", json={})
    assert reset_resp.status_code == 200
    reset_body = reset_resp.json()
    assert reset_body["ok"] is True
    get_after_reset = client.get("/profile")
    assert get_after_reset.status_code == 200
    assert len(get_after_reset.json()["versions"]) == initial_version_count + 2
    assert _version_field_subset(reset_body["active"]) == DEFAULT_PROFILE_FIELDS

    preview = client.get("/profile/prompt-preview")
    assert preview.status_code == 200
    prev = preview.json()
    assert prev["ok"] is True
    assert prev.get("profile_version_id")
    assert prev.get("prompt_hash", "").startswith("sha256:")
    prompt_text = prev.get("prompt") or prev.get("prompt_text") or ""
    assert "LOW-FIT" in prompt_text or "SUPPRESS" in prompt_text


def test_profile_prompt_preview_draft_query_overrides_negative(monkeypatch, tmp_path):
    _patch_profile_dir(monkeypatch, tmp_path)
    from condom_core.api.app import app

    client = TestClient(app)
    resp = client.get(
        "/profile/prompt-preview",
        params={"negative_profile": "draft_neg_marker_zzz"},
    )
    assert resp.status_code == 200
    data = resp.json()
    prompt_text = data.get("prompt") or data.get("prompt_text") or ""
    assert "draft_neg_marker_zzz" in prompt_text


def test_score_m3_batch_model_call_request_json_includes_profile_metadata(monkeypatch, tmp_path):
    from condom_core.ambient_m3 import score_m3_item_batch
    from condom_core.db import init_db, upsert_items
    from condom_core.models import Item

    _patch_profile_dir(monkeypatch, tmp_path)
    version = save_profile_version(
        {"negative_profile": "SCORING_NEG_MARKER_QRS"},
        source="test",
    )
    monkeypatch.setattr("condom_core.ambient_m3.get_key", lambda: "test-key")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    session_id = "sess-profile-m3"
    harvested = "2026-01-01T00:00:00+00:00"
    item = Item(
        item_id="p1",
        source="x_graphql",
        session_id=session_id,
        batch_id="r1",
        original_rank=1,
        author_handle="h",
        text="tweet",
        raw_json={},
    )
    upsert_items(conn, [item], harvested_at=harvested)
    conn.execute(
        """
        INSERT INTO candidate_window_items (
          session_id, item_id, window_rank, first_response_id, first_captured_at, source
        ) VALUES (?, ?, 1, ?, ?, ?)
        """,
        (session_id, "p1", "r1", harvested, "x_graphql"),
    )
    conn.commit()
    rows = [
        {
            "item_id": "p1",
            "window_rank": 1,
            "text": "tweet",
            "rendered_text": "<p1> tweet",
            "author_handle": "h",
            "link_url": None,
            "first_captured_at": harvested,
        }
    ]

    def fake_model(_prompt: str):
        return json.dumps(
            {
                "items": [
                    {
                        "item_id": "p1",
                        "score": 50,
                        "tier": "mid",
                        "serve": True,
                        "reason": "ok",
                    }
                ]
            }
        )

    score_m3_item_batch(conn, session_id, rows, fake_model)
    row = conn.execute(
        "SELECT request_json FROM model_calls WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    assert row is not None
    meta = json.loads(row["request_json"])
    assert meta.get("profile_version_id") == version["profile_version_id"]
    assert meta.get("prompt_hash", "").startswith("sha256:")
    assert "SCORING_NEG_MARKER_QRS" in (meta.get("prompt") or "")
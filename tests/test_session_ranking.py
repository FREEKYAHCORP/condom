from condom_core.profile import DEFAULT_POSITIVE_PROFILE, load_positive_profile_text
from condom_core import session_ranking
from condom_core.session_ranking import MODE_TO_ARM, resolve_arm


def test_mode_to_arm_api_modes():
    assert MODE_TO_ARM["native"] == "native_x_order"
    assert MODE_TO_ARM["cheap"] == "cheap_combo_v0"
    assert MODE_TO_ARM["m3"] == "llm_usersim_encounter"


def test_resolve_arm_aliases():
    assert resolve_arm("cheap") == "cheap_combo_v0"
    assert resolve_arm("bm25_saved_profile") == "bm25_saved_profile"
    assert resolve_arm("m3") == "llm_usersim_encounter"


def test_load_positive_profile_fallback(monkeypatch, tmp_path):
    from condom_core import profile as profile_mod

    monkeypatch.setattr(profile_mod, "PROFILE", tmp_path)
    assert load_positive_profile_text() == DEFAULT_POSITIVE_PROFILE

def test_m3_limit_batches_zero_means_uncapped():
    batches = [("b0", []), ("b1", [])]
    assert session_ranking._limit_batches(batches, None) == batches
    assert session_ranking._limit_batches(batches, 0) == batches
    assert session_ranking._limit_batches(batches, 1) == [("b0", [])]


def test_cheap_linear_zero_predictions_remains_skipped(monkeypatch):
    monkeypatch.setattr(session_ranking, "rebuild_session_order", lambda conn, session_id: None)
    monkeypatch.setattr(session_ranking, "_rank_cheap_linear", lambda conn, session_id, refresh: 0)
    result = session_ranking.rank_session_arm(object(), "s", "cheap_linear")
    assert result["status"] == "skipped"
    assert result["predictions"] == 0
    assert "implementation gated" in result["reason"]
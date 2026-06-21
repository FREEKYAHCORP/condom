from condom_core.profile import DEFAULT_POSITIVE_PROFILE, load_positive_profile_text
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
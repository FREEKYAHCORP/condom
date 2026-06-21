from condom_core.prompts import BANNED_ENCOUNTER_WORDS, build_prompt


def test_encounter_prompt_has_no_banned_words():
    prompt = build_prompt("@a\nhello\n<1>", "1")
    lower = prompt.lower()
    for word in BANNED_ENCOUNTER_WORDS:
        assert f" {word} " not in f" {lower} "


def test_m0_prompt_has_neutral_identity_and_no_event_fields():
    prompt = build_prompt("@a\nhello\n<1>", "1")
    assert "(not specified)" in prompt
    for field in ["obs_save", "visible_ms", "profile_open", "thread_open", "link_click", "lens_feedback"]:
        assert field not in prompt

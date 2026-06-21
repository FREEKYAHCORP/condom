from condom_core.prompts import (
    FEED_SELECTION_PROMPT_VERSION,
    build_feed_selection_prompt,
)

_JSON_KEYS = (
    "selected_feed",
    "selected_item_ids",
    "feed_scores",
    "curation_ratio_note",
    "predicted_behavior",
    "dropped_item_ids",
    "short_verdict",
)

_FEED_SECTION_LABELS = (
    "feed: precision",
    "feed: exploration",
    "feed: balanced",
)


def _sample_prompt(**kwargs) -> str:
    defaults = {
        "candidate_window": "id:9001 | topic:rust async\nid:9002 | topic:founder diary",
        "feed_precision": "<9001> precision slate item",
        "feed_exploration": "<9002> exploration slate item",
        "feed_balanced": "<9001> balanced mix",
    }
    defaults.update(kwargs)
    return build_feed_selection_prompt(**defaults)


def test_feed_selection_prompt_version_constant():
    assert FEED_SELECTION_PROMPT_VERSION == "usersim_feed_selection_v0"


def test_feed_selection_prompt_includes_three_feed_sections():
    prompt = _sample_prompt()
    for label in _FEED_SECTION_LABELS:
        assert label in prompt


def test_feed_selection_prompt_identity_defaults_when_empty():
    prompt = _sample_prompt()
    assert "(not specified)" in prompt
    assert "WHO I AM, REVEALED" in prompt
    assert "WHO I'M BECOMING, ENDORSED" in prompt


def test_feed_selection_prompt_injects_supplied_inputs():
    prompt = _sample_prompt(
        candidate_window="CANDIDATE_MARKER",
        feed_precision="PRECISION_MARKER",
        feed_exploration="EXPLORATION_MARKER",
        feed_balanced="BALANCED_MARKER",
        identity_revealed="revealed self",
        identity_endorsed="endorsed self",
        state_preamble="late night skim",
        curation_target="choose 12 items",
    )
    for marker in (
        "CANDIDATE_MARKER",
        "PRECISION_MARKER",
        "EXPLORATION_MARKER",
        "BALANCED_MARKER",
        "revealed self",
        "endorsed self",
        "late night skim",
        "choose 12 items",
    ):
        assert marker in prompt


def test_feed_selection_prompt_names_strict_json_keys():
    prompt = _sample_prompt()
    for key in _JSON_KEYS:
        assert f'"{key}"' in prompt


def test_feed_selection_prompt_candidate_window_only_swap_rule():
    prompt = _sample_prompt()
    assert "candidate window" in prompt.lower()
    assert "no invented ids" in prompt.lower() or "no items not listed" in prompt.lower()


def test_feed_selection_prompt_allows_raw_candidate_language():
    prompt = _sample_prompt(candidate_window="<9003> candidate text says should")
    assert "should" in prompt
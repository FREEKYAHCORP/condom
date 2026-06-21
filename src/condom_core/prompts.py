from __future__ import annotations

from .config import PROMPTS


PROMPT_VERSION = "usersim_encounter_v0"
BANNED_ENCOUNTER_WORDS = {
    "trap",
    "agency",
    "nourish",
    "regret",
    "regulation",
    "wellness",
    "healthy",
    "mindful",
    "doomscroll",
    "should",
}

FEED_SELECTION_PROMPT_VERSION = "usersim_feed_selection_v0"


def validate_encounter_prompt(text: str) -> None:
    import re

    hits = sorted(
        word for word in BANNED_ENCOUNTER_WORDS
        if re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE)
    )
    if hits:
        raise ValueError(f"Encounter prompt contains banned word(s): {', '.join(hits)}")


def build_prompt(
    rendered_timeline: str,
    first_item_id: str,
    identity_revealed: str = "",
    identity_endorsed: str = "",
    state_preamble: str = "ordinary scroll session. a few minutes to look around.",
) -> str:
    template = (PROMPTS / "usersim_encounter_v0.txt").read_text(encoding="utf-8")
    prompt = (
        template.replace("{{RENDERED_TIMELINE}}", rendered_timeline)
        .replace("{{STATE_PREAMBLE}}", state_preamble)
        .replace("{{IDENTITY_REVEALED}}", identity_revealed.strip() or "(not specified)")
        .replace("{{IDENTITY_ENDORSED}}", identity_endorsed.strip() or "(not specified)")
        .replace("{{FIRST_UNANSWERED_ITEM_PREFIX}}", f"<{first_item_id}> me:")
    )
    validate_encounter_prompt(prompt)
    return prompt


def build_feed_selection_prompt(
    candidate_window: str,
    feed_precision: str,
    feed_exploration: str,
    feed_balanced: str,
    identity_revealed: str = "",
    identity_endorsed: str = "",
    state_preamble: str = "ordinary scroll session. a few minutes to look around.",
    curation_target: str = "choose 10-15 items",
) -> str:
    template = (PROMPTS / "usersim_feed_selection_v0.txt").read_text(encoding="utf-8")
    validate_encounter_prompt(template)
    return (
        template.replace("{{CANDIDATE_WINDOW}}", candidate_window)
        .replace("{{FEED_PRECISION}}", feed_precision)
        .replace("{{FEED_EXPLORATION}}", feed_exploration)
        .replace("{{FEED_BALANCED}}", feed_balanced)
        .replace("{{STATE_PREAMBLE}}", state_preamble)
        .replace("{{IDENTITY_REVEALED}}", identity_revealed.strip() or "(not specified)")
        .replace("{{IDENTITY_ENDORSED}}", identity_endorsed.strip() or "(not specified)")
        .replace("{{CURATION_TARGET}}", curation_target)
    )


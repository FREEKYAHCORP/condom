from __future__ import annotations

from pathlib import Path

from condom_core.config import PROMPTS


def test_qualitative_judge_prompt_uses_x_returned_candidates_not_arm_leaks():
    text = (PROMPTS / "qualitative_feed_judge_v0.md").read_text(encoding="utf-8")
    body_start = text.index("```text") + len("```text")
    body_end = text.index("```", body_start)
    body = text[body_start:body_end]

    assert "x_returned_candidates" in body
    assert "native X order" not in body
    assert "cheap deterministic" not in body.lower()
    assert "LLM/MiniMax" not in body
    assert "minimax reranker" not in body.lower()
    assert "Do not try to guess the source system" in body
    assert "position number" in body.lower() or "position number in each feed" in body
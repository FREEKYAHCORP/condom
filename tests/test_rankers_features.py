from condom_core.rankers.cheap_combo import rank_combo
from condom_core.rankers.features import ml_frontier_lexicon_score, rank_text, research_url_bonus


def row(item_id, text, link_url=None):
    return {
        "item_id": item_id,
        "batch_id": "b0",
        "original_rank": int(item_id),
        "author_handle": "a",
        "author_name": "A",
        "author_bio": "ML researcher",
        "text": text,
        "quoted_text": None,
        "thread_context": None,
        "media_desc": None,
        "link_url": link_url,
        "link_title": None,
        "link_excerpt": None,
    }


def test_research_url_bonus_arxiv():
    assert research_url_bonus(row("1", "paper", "https://arxiv.org/abs/2601.12345")) == 1.0


def test_frontier_lexicon_capped():
    text = "diffusion JEPA eval harness interpretability benchmark latent reasoning"
    assert ml_frontier_lexicon_score(text) == 1.0


def test_rank_text_excludes_behavior_and_engagement():
    r = row("1", "hello")
    r["save"] = 1
    r["visible_ms"] = 1000
    r["engagement"] = {"favorite_count": 999}
    text = rank_text(r)
    assert "visible_ms" not in text
    assert "favorite_count" not in text
    assert "999" not in text


def test_combo_prefers_arxiv_paper_over_hot_take():
    rows = [
        row("1", "hot take everyone is wrong about AI", None),
        row("2", "new diffusion benchmark eval paper with code", "https://arxiv.org/abs/2601.00001"),
    ]
    ranked = rank_combo(rows, "diffusion benchmark eval paper arxiv")
    assert ranked[0][0]["item_id"] == "2"

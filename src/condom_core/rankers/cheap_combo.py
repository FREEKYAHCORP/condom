from __future__ import annotations

from .bm25 import rank_bm25
from .features import (
    code_like_count,
    discourse_penalty,
    duplicate_penalties,
    ml_frontier_lexicon_score,
    normalize,
    paper_shape_bonus,
    promo_penalty,
    rank_text,
    research_url_bonus,
)
from .tfidf import rank_tfidf


ARM = "cheap_combo_v0"


def rank_combo(rows: list[dict], positive_profile_text: str) -> list[tuple[dict, float]]:
    bm25_order = rank_bm25(rows, positive_profile_text)
    tfidf_order = rank_tfidf(rows, positive_profile_text)
    bm25_by_id = {row["item_id"]: score for row, score in bm25_order}
    tfidf_by_id = {row["item_id"]: score for row, score in tfidf_order}
    bm25_norm = dict(zip(bm25_by_id.keys(), normalize(list(bm25_by_id.values()))))
    tfidf_norm = dict(zip(tfidf_by_id.keys(), normalize(list(tfidf_by_id.values()))))

    duplicate_rows = sorted(rows, key=lambda r: (r["original_rank"] or 10**9, r["item_id"]))
    texts = [rank_text(row) for row in duplicate_rows]
    duplicate_by_id = {
        row["item_id"]: penalty for row, penalty in zip(duplicate_rows, duplicate_penalties(texts))
    }

    ranked: list[tuple[dict, float]] = []
    for row in rows:
        text = rank_text(row)
        link_or_thread_bonus = 1.0 if row["link_url"] or row["thread_context"] else 0.0
        media_or_code_bonus = 1.0 if row["media_desc"] or code_like_count(text) else 0.0
        frontier_bonus = max(ml_frontier_lexicon_score(text), paper_shape_bonus(text), research_url_bonus(row))
        score = (
            0.40 * bm25_norm.get(row["item_id"], 0.0)
            + 0.22 * tfidf_norm.get(row["item_id"], 0.0)
            + 0.16 * frontier_bonus
            + 0.08 * link_or_thread_bonus
            + 0.04 * media_or_code_bonus
            - 0.06 * duplicate_by_id.get(row["item_id"], 0.0)
            - 0.08 * discourse_penalty(text)
            - 0.04 * promo_penalty(text)
        )
        ranked.append((row, score))
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)

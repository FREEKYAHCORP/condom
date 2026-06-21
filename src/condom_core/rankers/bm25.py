from __future__ import annotations

from rank_bm25 import BM25Okapi

from .features import rank_text, tokenize


ARM = "bm25_saved_profile"


def rank_bm25(rows: list[dict], positive_profile_text: str) -> list[tuple[dict, float]]:
    corpus = [rank_text(row) for row in rows]
    tokenized = [tokenize(text) for text in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(tokenize(positive_profile_text)).tolist()
    return sorted(zip(rows, scores), key=lambda pair: pair[1], reverse=True)

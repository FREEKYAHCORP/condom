from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .features import rank_text


ARM = "tfidf_saved_profile"


def rank_tfidf(rows: list[dict], positive_profile_text: str) -> list[tuple[dict, float]]:
    docs = [positive_profile_text] + [rank_text(row) for row in rows]
    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_features=50000,
    )
    matrix = vectorizer.fit_transform(docs)
    scores = cosine_similarity(matrix[0], matrix[1:]).ravel().tolist()
    return sorted(zip(rows, scores), key=lambda pair: pair[1], reverse=True)

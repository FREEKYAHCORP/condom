from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import urlparse


DISCOURSE_MARKERS = [
    "takes",
    "discourse",
    "everyone is saying",
    "everyone is",
    "hot take",
    "people don't understand",
    "unpopular opinion",
    "normalize",
    "founder mode",
    "build in public",
    "secret to",
    "mother of all",
    "completely insane",
    "midwit",
    "quora",
    "follow for more",
    "like and rt",
]

FRONTIER_LEXICON_V1 = [
    "diffusion",
    "jepa",
    "world model",
    "interpretability",
    "benchmark",
    "benchmarks",
    "eval",
    "evals",
    "harness",
    "agent harness",
    "tool use",
    "fine-tune",
    "finetune",
    "open-weight",
    "open weights",
    "sota",
    "icml",
    "neurips",
    "iclr",
    "latent",
    "reasoning model",
    "tpot",
    "transformer",
    "rl",
    "scaling law",
]

RESEARCH_HOSTS = {
    "arxiv.org",
    "openreview.net",
    "aclanthology.org",
    "huggingface.co",
    "github.com",
    "gitlab.com",
    "semanticscholar.org",
    "doi.org",
    "paperswithcode.com",
}


def _host_path(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.netloc:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path[:80]
    return f"{host}{path}"


def _get(row, key: str):
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


def rank_text(row) -> str:
    link_hint = _host_path(_get(row, "link_url"))
    parts = [
        _get(row, "author_handle"),
        _get(row, "author_name"),
        _get(row, "author_bio"),
        _get(row, "text"),
        _get(row, "quoted_text"),
        _get(row, "thread_context"),
        _get(row, "media_desc"),
        _get(row, "link_title"),
        _get(row, "link_excerpt"),
        link_hint,
    ]
    return "\n".join(str(p) for p in parts if p)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_#@]+", (text or "").lower())


def percentile_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    pairs = sorted((score, idx) for idx, score in enumerate(scores))
    out = [0.0] * len(scores)
    denom = max(1, len(scores) - 1)
    for rank, (_score, idx) in enumerate(pairs):
        out[idx] = rank / denom
    return out


def normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if math.isclose(lo, hi):
        return [0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def code_like_count(text: str) -> int:
    return len(re.findall(r"(```|def |class |import |const |=>|==|::|/api/|\\.py|\\.ts|\\.js)", text or ""))


def discourse_penalty(text: str) -> float:
    haystack = (text or "").lower()
    hits = sum(1 for marker in DISCOURSE_MARKERS if marker in haystack)
    return min(1.0, hits / 3.0)


def promo_penalty(text: str) -> float:
    haystack = (text or "").lower()
    markers = ["dms open", "dm me", "waitlist", "sign up", "my newsletter", "now hiring", "apply now"]
    return min(1.0, sum(1 for marker in markers if marker in haystack) / 2.0)


def research_url_bonus(row) -> float:
    hint = _host_path(_get(row, "link_url"))
    if not hint:
        return 0.0
    host = hint.split("/", 1)[0]
    if host in RESEARCH_HOSTS:
        return 1.0
    if hint.endswith(".pdf") or "/pdf" in hint:
        return 0.8
    return 0.0


def ml_frontier_lexicon_score(text: str) -> float:
    haystack = (text or "").lower()
    hits = sum(1 for marker in FRONTIER_LEXICON_V1 if marker in haystack)
    return min(1.0, hits / 4.0)


def paper_shape_bonus(text: str) -> float:
    haystack = (text or "").lower()
    patterns = [
        r"\barxiv\b",
        r"\bopenreview\b",
        r"\babstract\b",
        r"\bauthors?:\b",
        r"\bwe (release|introduce|present|propose|evaluate)\b",
        r"\b(icml|neurips|iclr|acl|emnlp)\b",
    ]
    hits = sum(1 for pat in patterns if re.search(pat, haystack))
    return min(1.0, hits / 3.0)


def duplicate_penalties(texts: list[str]) -> list[float]:
    penalties: list[float] = []
    seen: list[Counter[str]] = []
    for text in texts:
        tokens = Counter(tokenize(text))
        if not tokens:
            penalties.append(0.0)
            seen.append(tokens)
            continue
        best = 0.0
        norm = sum(v * v for v in tokens.values()) ** 0.5
        for prior in seen:
            prior_norm = sum(v * v for v in prior.values()) ** 0.5
            if not prior_norm:
                continue
            dot = sum(tokens[k] * prior[k] for k in tokens.keys() & prior.keys())
            best = max(best, dot / (norm * prior_norm))
        penalties.append(best)
        seen.append(tokens)
    return penalties

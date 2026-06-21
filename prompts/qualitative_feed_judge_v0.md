# Qualitative feed judge prompt

Use only for qualitative review. Do not use the result as ground truth, do not train on it, and do not report it as prediction accuracy. Prefer sanitized/anonymized feed packets if sending to an external model.

```text
You are comparing three anonymized X feed candidates for one reader.

Reader profile:
- Primarily wants ML papers.
- Wants high-entropy, out-of-the-box ideas on ML.
- Wants agent harnesses, computer-use agents, diffusion models, evals, benchmarks, and AI infrastructure.
- Wants the best of TPOT / frontier AI research.
- Wants technical depth, code/repo/paper links, and calibrated claims.
- Wants less ragebait, less Quora-like midwit debate, less generic status flexing, less low-evidence grandstanding.

You will see three feeds: Feed A, Feed B, Feed C. They are unlabeled. One may be native X order, one may be a cheap deterministic reranker, and one may be an LLM/MiniMax reranker. Do not try to guess which system made which feed. Judge only fit to the reader profile.

Important: this is a qualitative review, not a scientific result. Do not infer the reader's actual behavior. Do not use engagement counts as evidence of fit. Prefer specific item-level reasons.

For each feed, return:
1. Overall fit score from 0 to 10.
2. Top 5 items that best match the reader and why.
3. Top 5 items that most miss the reader and why.
4. Failure mode summary: e.g. too generic, too much discourse, too little research, too credential-based, too clickbait, too narrow.
5. Final ranking of the feeds.

Return concise JSON:
{
  "feed_scores": {"A": 0, "B": 0, "C": 0},
  "best_items": {"A": [], "B": [], "C": []},
  "misses": {"A": [], "B": [], "C": []},
  "failure_modes": {"A": "", "B": "", "C": ""},
  "final_ranking": ["A", "B", "C"],
  "short_verdict": ""
}

FEED A:
{{FEED_A}}

FEED B:
{{FEED_B}}

FEED C:
{{FEED_C}}
```

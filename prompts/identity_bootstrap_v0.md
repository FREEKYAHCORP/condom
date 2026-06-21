# Identity bootstrap v0 prompt (M1 only)

Use this only after M0 has a real no-identity baseline. It drafts an IDENTITY candidate from prior positives and the user's first-person note. The candidate is not proof; it becomes useful only if replay improves the held-out behavior score.

Forbidden words in the returned identity: trap, agency, nourish, regret, regulation, wellness, healthy, mindful, doomscroll, should.

```text
You are drafting the compact IDENTITY document for a local feed prediction harness.

The feed model reads this document before predicting what I would stop on, open, save, and look at. The document has to help prediction, not flatter me. Keep it first-person. Keep it short. Separate what pulls me from what I deliberately want more of.

Inputs:
1. Prior positive items I liked/bookmarked/saved, rendered as timeline text.
2. My own first-person note.

Return exactly this JSON shape:
{
  "revealed": "WHO I AM, REVEALED — first-person prose about what actually catches me, including statusy or compulsive pulls if they recur. No moral lecture.",
  "endorsed": "WHO I'M BECOMING, ENDORSED — first-person prose about what I save, return to, and want in front of me on purpose.",
  "never_serve": ["categories that may catch attention but must not be selected into a feed"],
  "changelog": "one sentence explaining the bootstrap"
}

My first-person note:
I primarily want ML papers, high-entropy and out-of-the-box ideas on ML, agent harnesses, diffusion models, and the best of the TPOT / AI frontier. I want technical depth, new research directions, good evals, infrastructure, and code-backed work. I want less ragebait, less Quora-like midwit debate, less generic status flexing, and less low-evidence grandstanding.

Prior positive items:
{{RENDERED_POSITIVE_TIMELINE}}
```

from __future__ import annotations

import re


ITEM_START_RE = re.compile(r"(?:<|⟦)(?P<id>[^>⟧]+)(?:>|⟧)\s*me:\s*", re.IGNORECASE)
METRICS_RE = re.compile(
    r"(?:^|\n)\s*(?:->|→)?\s*"
    r"stop\s*:\s*(?P<stop>y|n|yes|no|1|0)\s*,?\s+"
    r"open\s*:\s*(?P<open>y|n|yes|no|1|0)\s*,?\s+"
    r"save\s*:\s*(?P<save>y|n|yes|no|1|0)\s*,?\s+"
    r"look\s*:\s*(?P<look>[0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE | re.MULTILINE,
)


def _boolish(value: str) -> int:
    return 1 if value.strip().lower() in {"y", "yes", "1"} else 0


def parse_llm_output(text: str) -> list[dict]:
    """Parse tolerant Encounter output.

    A malformed item block is skipped, not raised. This keeps MiniMax format
    drift from crashing the harness.
    """
    source = text or ""
    starts = list(ITEM_START_RE.finditer(source))
    rows = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(source)
        block = source[start.end():end]
        metrics = METRICS_RE.search(block)
        if not metrics:
            continue
        reaction = block[: metrics.start()].strip()
        rows.append(
            {
                "item_id": start.group("id").strip(),
                "reaction_text": " ".join(reaction.split()),
                "pred_stop": _boolish(metrics.group("stop")),
                "pred_open": _boolish(metrics.group("open")),
                "pred_save": _boolish(metrics.group("save")),
                "pred_look_sec": float(metrics.group("look")),
            }
        )
    return rows

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


FEED_SELECTION_FEEDS = frozenset({"precision", "exploration", "balanced"})


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"y", "yes", "true", "1"}
    raise ValueError(f"invalid boolean value: {value!r}")


def _normalize_look_sec(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("look_sec must be numeric")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid look_sec: {value!r}") from exc
    return max(0.0, out)


def parse_feed_selection_json(
    text: str,
    candidate_ids: list[str] | set[str],
    target_n: int | None = None,
) -> dict:
    """Strict JSON validator for usersim_feed_selection_v0 model output."""
    import json as _json

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        payload = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("feed selection JSON must be an object")

    allowed = {str(cid).strip() for cid in candidate_ids if str(cid).strip()}
    if not allowed:
        raise ValueError("candidate_ids must be non-empty")

    selected_feed = payload.get("selected_feed")
    if not isinstance(selected_feed, str) or selected_feed.strip().lower() not in FEED_SELECTION_FEEDS:
        raise ValueError("selected_feed must be precision, exploration, or balanced")
    selected_feed = selected_feed.strip().lower()

    selected_item_ids = payload.get("selected_item_ids")
    if not isinstance(selected_item_ids, list) or not selected_item_ids:
        raise ValueError("selected_item_ids must be a non-empty list")
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for item in selected_item_ids:
        if not isinstance(item, str):
            raise ValueError("selected_item_ids entries must be strings")
        item_id = item.strip()
        if not item_id:
            raise ValueError("selected_item_ids must not contain blank ids")
        if item_id not in allowed:
            raise ValueError(f"invented selected id not in candidate window: {item_id}")
        if item_id in seen:
            raise ValueError(f"duplicate selected_item_id: {item_id}")
        seen.add(item_id)
        normalized_ids.append(item_id)

    if target_n is not None and len(normalized_ids) != target_n:
        raise ValueError(f"selected_item_ids length {len(normalized_ids)} != target_n {target_n}")

    predicted_behavior = payload.get("predicted_behavior")
    if not isinstance(predicted_behavior, dict):
        raise ValueError("predicted_behavior must be an object")
    behavior_by_id = {
        str(key).strip(): value
        for key, value in predicted_behavior.items()
        if str(key).strip()
    }

    def _near_behavior_key(item_id: str) -> str | None:
        candidates = []
        for key in behavior_by_id:
            if key in seen or key in allowed or len(key) != len(item_id):
                continue
            distance = sum(1 for left, right in zip(key, item_id) if left != right)
            if distance <= 2:
                candidates.append(key)
        return candidates[0] if len(candidates) == 1 else None

    normalized_behavior: dict[str, dict] = {}
    for item_id in normalized_ids:
        behavior_key = item_id if item_id in behavior_by_id else _near_behavior_key(item_id)
        if behavior_key is None:
            raise ValueError(f"missing predicted_behavior for selected id: {item_id}")
        entry = behavior_by_id[behavior_key]
        if not isinstance(entry, dict):
            raise ValueError(f"predicted_behavior[{item_id}] must be an object")
        for key in ("stop", "open", "save", "look_sec"):
            if key not in entry:
                raise ValueError(f"predicted_behavior[{item_id}] missing {key}")
        why = entry.get("why", "")
        if why is not None and not isinstance(why, str):
            raise ValueError(f"predicted_behavior[{item_id}].why must be a string")
        normalized_behavior[item_id] = {
            "stop": _normalize_bool(entry["stop"]),
            "open": _normalize_bool(entry["open"]),
            "save": _normalize_bool(entry["save"]),
            "look_sec": _normalize_look_sec(entry["look_sec"]),
            "why": (why or "").strip(),
        }

    for key in behavior_by_id:
        if key not in seen and key in allowed:
            raise ValueError(f"predicted_behavior contains unselected candidate id: {key}")

    feed_scores = payload.get("feed_scores")
    if not isinstance(feed_scores, dict):
        raise ValueError("feed_scores must be an object")
    for label in ("precision", "exploration", "balanced"):
        if label not in feed_scores:
            raise ValueError(f"feed_scores missing {label}")
        try:
            float(feed_scores[label])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"feed_scores[{label}] must be numeric") from exc

    dropped_item_ids = payload.get("dropped_item_ids")
    if dropped_item_ids is None:
        dropped_item_ids = []
    if not isinstance(dropped_item_ids, list):
        raise ValueError("dropped_item_ids must be a list")
    normalized_dropped: list[str] = []
    for item in dropped_item_ids:
        if not isinstance(item, str):
            raise ValueError("dropped_item_ids entries must be strings")
        item_id = item.strip()
        if not item_id:
            continue
        if item_id not in allowed:
            continue
        normalized_dropped.append(item_id)

    short_verdict = payload.get("short_verdict", "")
    if short_verdict is not None and not isinstance(short_verdict, str):
        raise ValueError("short_verdict must be a string")
    curation_ratio_note = payload.get("curation_ratio_note", "")
    if curation_ratio_note is not None and not isinstance(curation_ratio_note, str):
        raise ValueError("curation_ratio_note must be a string")

    return {
        "selected_feed": selected_feed,
        "selected_item_ids": normalized_ids,
        "feed_scores": {
            "precision": float(feed_scores["precision"]),
            "exploration": float(feed_scores["exploration"]),
            "balanced": float(feed_scores["balanced"]),
        },
        "curation_ratio_note": (curation_ratio_note or "").strip(),
        "predicted_behavior": normalized_behavior,
        "dropped_item_ids": normalized_dropped,
        "short_verdict": (short_verdict or "").strip(),
    }

from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import PROMPTS, RUNS
from .feed_generation import M3_FEED_ARM
from .rankers.cheap_combo import ARM as CHEAP_COMBO_ARM
from .rankers.native import ARM as NATIVE_ARM

DEFAULT_JUDGE_ARMS = (NATIVE_ARM, CHEAP_COMBO_ARM, M3_FEED_ARM)
FEED_LABELS = ("A", "B", "C")
QUAL_JUDGE_PROMPT = "qualitative_feed_judge_v0.md"

_LEAKAGE_ARM_TOKENS = (
    "native_x_order",
    "cheap_combo_v0",
    "m3_feed_selection_v0",
    "llm_usersim_encounter",
    "cheap_combo",
    "m3_feed",
    "native/cheap",
    "cheap deterministic",
    "llm/minimax",
    "minimax reranker",
)
_LEAKAGE_META_TOKENS = (
    "feed_run_id",
    "item_id",
    "window_rank",
    "original_rank",
    "pred_stop",
    "pred_save",
    "model_name",
    "model_call_id",
    "metrics_json",
    "coverage_ratio",
    "curation_ratio",
)
_SCORE_FIELD_RE = re.compile(
    r"(?i)\b(score|rank|prediction_id|reaction_text)\s*[=:]",
)
_ITEM_ID_LINE_RE = re.compile(r"(?i)^\s*id:\s*\S+")
_REST_ID_RE = re.compile(r"\b\d{15,20}\b")


def latest_feed_run_id(conn: sqlite3.Connection, *, session_id: str, arm: str) -> str | None:
    row = conn.execute(
        """
        SELECT feed_run_id
        FROM feed_runs
        WHERE session_id = ? AND arm = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id, arm),
    ).fetchone()
    return None if row is None else str(row["feed_run_id"])


def load_feed_run_sanitized_items(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    limit: int | None = None,
) -> list[dict[str, str]]:
    sql = """
        SELECT fri.feed_rank, i.author_handle, i.text, i.rendered_text
        FROM feed_run_items AS fri
        JOIN items AS i ON i.item_id = fri.item_id
        WHERE fri.feed_run_id = ?
        ORDER BY fri.feed_rank ASC
    """
    params: list[Any] = [feed_run_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, str]] = []
    for row in rows:
        body = (row["text"] or row["rendered_text"] or "").strip()
        body = " ".join(body.split())
        handle = (row["author_handle"] or "unknown").strip()
        if handle.startswith("@"):
            handle = handle[1:]
        out.append({"author": handle, "text": body})
    return out


def render_blind_feed_text(items: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for idx, item in enumerate(items, start=1):
        author = item.get("author") or "unknown"
        text = item.get("text") or ""
        blocks.append(f"{idx}. @{author}\n{text}")
    return "\n\n".join(blocks)


def _extract_prompt_body(template: str) -> str:
    marker = "```text"
    if marker not in template:
        return template
    start = template.index(marker) + len(marker)
    end = template.index("```", start)
    return template[start:end].strip("\n")


def _fill_prompt(template: str, feeds: dict[str, str]) -> str:
    body = _extract_prompt_body(template)
    for label, text in feeds.items():
        body = body.replace(f"{{{{FEED_{label}}}}}", text)
    return body


def check_packet_leakage(
    packet_text: str,
    *,
    secret_tokens: Iterable[str] | None = None,
) -> dict[str, Any]:
    lowered = packet_text.lower()
    hits: list[str] = []

    for token in _LEAKAGE_ARM_TOKENS + _LEAKAGE_META_TOKENS:
        if token.lower() in lowered:
            hits.append(f"token:{token}")

    if _SCORE_FIELD_RE.search(packet_text):
        hits.append("pattern:score_or_rank_field")

    for line in packet_text.splitlines():
        if _ITEM_ID_LINE_RE.match(line):
            hits.append("pattern:item_id_line")
            break

    if _REST_ID_RE.search(packet_text):
        hits.append("pattern:long_numeric_id")

    if secret_tokens:
        for token in secret_tokens:
            if not token:
                continue
            if token.lower() in lowered:
                hits.append(f"secret:{token[:48]}")

    return {"ok": len(hits) == 0, "hits": hits}


def build_blind_judge_packet(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    limit: int | None = None,
    seed: int = 7,
    arms: tuple[str, ...] = DEFAULT_JUDGE_ARMS,
    output_dir: Path | None = None,
    prompt_name: str = QUAL_JUDGE_PROMPT,
) -> dict[str, Any]:
    missing: list[str] = []
    run_by_arm: dict[str, str] = {}
    for arm in arms:
        feed_run_id = latest_feed_run_id(conn, session_id=session_id, arm=arm)
        if not feed_run_id:
            missing.append(arm)
            continue
        run_by_arm[arm] = feed_run_id
    if missing:
        raise ValueError(f"Missing latest feed_runs for session {session_id}: {', '.join(missing)}")

    labels = list(FEED_LABELS)
    arm_list = list(arms)
    rng = random.Random(seed)
    rng.shuffle(arm_list)
    label_to_arm = dict(zip(labels, arm_list))
    arm_to_label = {arm: label for label, arm in label_to_arm.items()}

    template = (PROMPTS / prompt_name).read_text(encoding="utf-8")
    feeds_for_prompt: dict[str, str] = {}
    feed_item_counts: dict[str, int] = {}
    for label in labels:
        arm = label_to_arm[label]
        items = load_feed_run_sanitized_items(conn, feed_run_id=run_by_arm[arm], limit=limit)
        feeds_for_prompt[label] = render_blind_feed_text(items)
        feed_item_counts[label] = len(items)

    packet_body = _fill_prompt(template, feeds_for_prompt)

    secret_tokens: list[str] = []
    for arm, feed_run_id in run_by_arm.items():
        secret_tokens.append(arm)
        secret_tokens.append(feed_run_id)
        row = conn.execute(
            "SELECT model_name FROM feed_runs WHERE feed_run_id = ?",
            (feed_run_id,),
        ).fetchone()
        if row and row["model_name"]:
            secret_tokens.append(str(row["model_name"]))
    feed_blob = "\n\n".join(feeds_for_prompt.values())
    leakage = check_packet_leakage(feed_blob, secret_tokens=secret_tokens)

    run_dir = output_dir or (RUNS / "judge_packets")
    run_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{session_id}_qual_judge"
    packet_path = run_dir / f"{stem}.md"
    key_path = run_dir / f"{stem}_key.json"
    meta_path = run_dir / f"{stem}_meta.json"

    packet_path.write_text(packet_body, encoding="utf-8")

    key_payload = {
        "session_id": session_id,
        "seed": seed,
        "labels": {label: label_to_arm[label] for label in labels},
        "feed_run_ids": {label_to_arm[label]: run_by_arm[label_to_arm[label]] for label in labels},
    }
    key_path.write_text(json.dumps(key_payload, indent=2) + "\n", encoding="utf-8")

    meta_payload = {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": prompt_name,
        "seed": seed,
        "limit": limit,
        "arms": list(arms),
        "label_to_arm": label_to_arm,
        "arm_to_label": arm_to_label,
        "feed_run_ids": run_by_arm,
        "feed_item_counts": feed_item_counts,
        "leakage": leakage,
        "paths": {
            "packet": str(packet_path),
            "key": str(key_path),
            "meta": str(meta_path),
        },
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2) + "\n", encoding="utf-8")

    return {
        "packet_path": packet_path,
        "key_path": key_path,
        "meta_path": meta_path,
        "label_to_arm": label_to_arm,
        "feed_run_ids": run_by_arm,
        "leakage": leakage,
        "meta": meta_payload,
    }


def insert_feed_evaluation(
    conn: sqlite3.Connection,
    *,
    evaluation_id: str,
    session_id: str,
    packet_path: str,
    key_path: str | None,
    meta_path: str | None,
    prompt_version: str,
    label_map_json: str,
    leakage_ok: bool,
    leakage_json: str,
    judge_result_json: str | None = None,
    created_at: str | None = None,
) -> None:
    ts = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO feed_evaluations (
          evaluation_id, session_id, packet_path, key_path, meta_path,
          prompt_version, label_map_json, leakage_ok, leakage_json,
          judge_result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation_id,
            session_id,
            packet_path,
            key_path,
            meta_path,
            prompt_version,
            label_map_json,
            1 if leakage_ok else 0,
            leakage_json,
            judge_result_json,
            ts,
        ),
    )
    conn.commit()


def load_feed_evaluation(conn: sqlite3.Connection, evaluation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM feed_evaluations WHERE evaluation_id = ?",
        (evaluation_id,),
    ).fetchone()
    return None if row is None else dict(row)


def store_judge_result_json(
    conn: sqlite3.Connection,
    *,
    evaluation_id: str,
    judge_result: dict[str, Any] | str,
) -> None:
    payload = judge_result if isinstance(judge_result, str) else json.dumps(judge_result)
    conn.execute(
        "UPDATE feed_evaluations SET judge_result_json = ? WHERE evaluation_id = ?",
        (payload, evaluation_id),
    )
    conn.commit()
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .feed_generation import M3_FEED_ARM
from .rankers.cheap_combo import ARM as CHEAP_COMBO_ARM
from .rankers.native import ARM as NATIVE_ARM

FEED_ARM_LABELS: dict[str, str] = {
    NATIVE_ARM: "Native X",
    CHEAP_COMBO_ARM: "Cheap Combo",
    M3_FEED_ARM: "M3 Feed Selection",
    "bm25_saved_profile": "BM25 Profile",
    "tfidf_saved_profile": "TF-IDF Profile",
    "llm_usersim_encounter": "LLM UserSim",
}

PREFERRED_SIDE_BY_SIDE_ARMS: tuple[str, ...] = (
    NATIVE_ARM,
    CHEAP_COMBO_ARM,
    M3_FEED_ARM,
)

FEED_RUN_ARM_ORDER: tuple[str, ...] = (
    NATIVE_ARM,
    "bm25_saved_profile",
    "tfidf_saved_profile",
    CHEAP_COMBO_ARM,
    M3_FEED_ARM,
    "llm_usersim_encounter",
)


def arm_display_label(arm: str) -> str:
    return FEED_ARM_LABELS.get(arm, arm)


def load_latest_feed_runs(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Latest feed_runs row per arm for a session, in FEED_RUN_ARM_ORDER."""
    rows = conn.execute(
        """
        SELECT fr.*
        FROM feed_runs AS fr
        INNER JOIN (
          SELECT arm, MAX(created_at) AS max_created
          FROM feed_runs
          WHERE session_id = ?
          GROUP BY arm
        ) AS latest
          ON latest.arm = fr.arm AND latest.max_created = fr.created_at
        WHERE fr.session_id = ?
        """,
        (session_id, session_id),
    ).fetchall()
    by_arm = {str(row["arm"]): dict(row) for row in rows}
    ordered = [by_arm[arm] for arm in FEED_RUN_ARM_ORDER if arm in by_arm]
    for arm in sorted(by_arm.keys() - set(FEED_RUN_ARM_ORDER)):
        ordered.append(by_arm[arm])
    return ordered


def _fmt_ratio(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def feed_run_ratio_card_html(row: dict[str, Any], *, escape) -> str:
    arm = str(row.get("arm") or "")
    metrics_raw = row.get("metrics_json")
    extra: dict[str, Any] = {}
    if metrics_raw:
        try:
            extra = json.loads(metrics_raw) if isinstance(metrics_raw, str) else dict(metrics_raw)
        except (TypeError, json.JSONDecodeError):
            extra = {}
    prefilter = row.get("prefilter_count")
    prefilter_ratio = row.get("prefilter_ratio")
    if prefilter is not None:
        prefilter_line = (
            f"x_returned_candidates prefilter {escape(prefilter)} "
            f"({_fmt_ratio(prefilter_ratio)} of window)"
        )
    else:
        prefilter_line = "scored full x_returned_candidates window"
    return f"""
<div class="metric feed-run-card">
  <b>{escape(arm_display_label(arm))}</b>
  <div class="muted">{escape(arm)}</div>
  <div>candidate window {escape(row.get('candidate_count'))}</div>
  <div>scored {escape(row.get('scored_count'))} · curated {escape(row.get('curated_count'))}/{escape(row.get('curated_k'))}</div>
  <div>coverage {_fmt_ratio(row.get('coverage_ratio'))} · curation {_fmt_ratio(row.get('curation_ratio'))}</div>
  <div>{prefilter_line}</div>
</div>
"""


def render_feed_run_ratio_cards(rows: list[dict[str, Any]], *, escape) -> str:
    if not rows:
        return '<p class="muted">No feed_runs rows for this session yet.</p>'
    cards = [feed_run_ratio_card_html(row, escape=escape) for row in rows]
    return f'<div class="grid metrics feed-run-metrics">{"".join(cards)}</div>'
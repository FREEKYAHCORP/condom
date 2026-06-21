from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import DB_PATH, RUNS
from condom_core.db import connect
from condom_core.feed_report import (
    PREFERRED_SIDE_BY_SIDE_ARMS,
    arm_display_label,
    load_latest_feed_runs,
    render_feed_run_ratio_cards,
)


STYLE = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #14171f;
  --muted: #657080;
  --line: #dde2ea;
  --accent: #0b6bcb;
  --good: #087f5b;
  --warn: #b35c00;
  --bad: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.shell { max-width: 1440px; margin: 0 auto; padding: 20px; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(246, 247, 249, 0.96);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(8px);
}
.topbar .shell { padding-top: 12px; padding-bottom: 12px; }
h1 { margin: 0 0 6px; font-size: 22px; font-weight: 700; letter-spacing: 0; }
h2 { margin: 24px 0 12px; font-size: 18px; letter-spacing: 0; }
h3 { margin: 0 0 8px; font-size: 15px; letter-spacing: 0; }
.muted { color: var(--muted); }
.nav { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.nav a, button, .pill {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--ink);
  border-radius: 7px;
  padding: 7px 10px;
  font: inherit;
}
button { cursor: pointer; }
button:hover, .nav a:hover { border-color: #aeb8c6; text-decoration: none; }
.grid { display: grid; gap: 12px; }
.metrics { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}
.metric b { display: block; font-size: 18px; }
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 16px 0;
}
.search { min-width: 260px; flex: 1; padding: 9px 10px; border: 1px solid var(--line); border-radius: 7px; }
.batch {
  margin: 18px 0;
  border: 1px solid var(--line);
  background: #fbfcfd;
  border-radius: 8px;
  overflow: hidden;
}
.batch-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: #edf1f6;
  border-bottom: 1px solid var(--line);
}
.feed { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 10px; padding: 10px; }
.side-feed { grid-template-columns: repeat(3, minmax(280px, 1fr)); align-items: start; }
.column {
  min-width: 0;
  background: #f8fafc;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px;
}
.column-title {
  position: sticky;
  top: 86px;
  z-index: 4;
  background: #f8fafc;
  padding: 4px 2px 8px;
  font-weight: 700;
}
.tweet {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  margin: 0 0 8px;
}
.tweet[data-label="save"] { border-color: #79c2a8; box-shadow: 0 0 0 1px #79c2a8 inset; }
.tweet[data-label="open"] { border-color: #7ab5e8; box-shadow: 0 0 0 1px #7ab5e8 inset; }
.tweet[data-label="skip"] { opacity: 0.72; }
.tweet-head { display: flex; justify-content: space-between; gap: 8px; }
.author { font-weight: 700; overflow-wrap: anywhere; }
.rank { color: var(--muted); white-space: nowrap; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
.reaction { margin: 8px 0; color: #3c4958; border-left: 3px solid #b7c4d4; padding-left: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.chip {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 7px;
  color: #344052;
  background: #f7f9fb;
  font-size: 12px;
}
.chip.y { color: var(--good); border-color: #add9ca; background: #effaf5; }
.chip.n { color: #687385; }
.chip.warn { color: var(--warn); border-color: #edc48f; background: #fff7eb; }
.labels { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.labels button { padding: 4px 8px; font-size: 12px; }
.labels button.active { border-color: var(--accent); background: #e8f2ff; color: #084f99; }
.hidden { display: none !important; }
table { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: #edf1f6; }
@media (max-width: 1050px) {
  .side-feed { grid-template-columns: 1fr; }
  .column-title { top: 82px; }
}
"""


SCRIPT = """
const LABEL_KEY = "condom_coreeriment_labels::" + document.body.dataset.sessionId;

function readLabels() {
  try { return JSON.parse(localStorage.getItem(LABEL_KEY) || "{}"); }
  catch { return {}; }
}

function writeLabels(labels) {
  localStorage.setItem(LABEL_KEY, JSON.stringify(labels));
}

function applyLabels() {
  const labels = readLabels();
  for (const card of document.querySelectorAll("[data-item-id]")) {
    const value = labels[card.dataset.itemId]?.label || "";
    card.dataset.label = value;
    for (const button of card.querySelectorAll("[data-label-action]")) {
      button.classList.toggle("active", button.dataset.labelAction === value);
    }
  }
  const count = Object.keys(labels).length;
  const el = document.querySelector("[data-label-count]");
  if (el) el.textContent = String(count);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-label-action]");
  if (!button) return;
  const card = button.closest("[data-item-id]");
  const labels = readLabels();
  const itemId = card.dataset.itemId;
  const action = button.dataset.labelAction;
  if (labels[itemId]?.label === action) delete labels[itemId];
  else labels[itemId] = { item_id: itemId, label: action, ts: new Date().toISOString() };
  writeLabels(labels);
  applyLabels();
});

document.addEventListener("input", (event) => {
  if (!event.target.matches("[data-search]")) return;
  const q = event.target.value.toLowerCase();
  for (const card of document.querySelectorAll(".tweet")) {
    card.classList.toggle("hidden", q && !card.innerText.toLowerCase().includes(q));
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-export-labels]");
  if (!button) return;
  const labels = readLabels();
  const blob = new Blob([JSON.stringify(Object.values(labels), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "manual_labels.json";
  a.click();
  URL.revokeObjectURL(url);
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-show]");
  if (!button) return;
  const mode = button.dataset.show;
  document.body.dataset.showMode = mode;
  for (const card of document.querySelectorAll(".tweet")) {
    const inTop = card.dataset.topK === "1";
    card.classList.toggle("hidden", mode === "top" && !inTop);
  }
});

applyLabels();
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)


def latest_run_dir() -> Path | None:
    dirs = sorted([p for p in RUNS.iterdir() if p.is_dir()], reverse=True)
    return dirs[0] if dirs else None


def fmt_num(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def tweet_url(row: dict) -> str | None:
    handle = row.get("author_handle")
    item_id = row.get("item_id")
    if handle and item_id and str(item_id).isdigit():
        return f"https://x.com/{handle}/status/{item_id}"
    return None


def load_rows(conn, session_id: str, arm: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          i.item_id, i.batch_id, i.original_rank, i.author_handle, i.author_name,
          i.text, i.rendered_text,
          p.rank, p.score, p.pred_stop, p.pred_open, p.pred_save,
          p.pred_look_sec, p.reaction_text,
          COALESCE(MAX(e.save), 0) AS obs_save,
          COALESCE(MAX(e.stop), 0) AS obs_stop,
          COALESCE(MAX(e.thread_open), 0) AS thread_open,
          COALESCE(MAX(e.profile_open), 0) AS profile_open,
          COALESCE(MAX(e.link_click), 0) AS link_click,
          COALESCE(MAX(e.look_sec), 0) AS obs_look
        FROM items i
        LEFT JOIN arm_predictions p
          ON p.item_id = i.item_id
         AND p.session_id = i.session_id
         AND p.arm = ?
        LEFT JOIN events e
          ON e.item_id = i.item_id
         AND e.session_id = i.session_id
         AND e.exposed = 1
        WHERE i.session_id = ?
        GROUP BY i.item_id
        ORDER BY i.batch_id, CASE WHEN p.rank IS NULL THEN 999999 ELSE p.rank END, i.original_rank
        """,
        (arm, session_id),
    ).fetchall()
    return [dict(row) for row in rows]


def load_scores(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM scores
        WHERE session_id=?
        ORDER BY save_fbeta DESC, false_skip_saves ASC, false_pull_count ASC
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def arms_for_session(conn, session_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT arm, COUNT(*) AS n
        FROM arm_predictions
        WHERE session_id=?
        GROUP BY arm
        ORDER BY arm
        """,
        (session_id,),
    ).fetchall()
    order = ["native_x_order", "bm25_saved_profile", "tfidf_saved_profile", "cheap_combo_v0", "m3_feed_selection_v0", "llm_usersim_encounter"]
    present = {row["arm"] for row in rows}
    return [arm for arm in order if arm in present] + sorted(present - set(order))


def page(title: str, session_id: str, body: str, nav: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>{STYLE}</style>
</head>
<body data-session-id="{esc(session_id)}" data-show-mode="all">
  <div class="topbar">
    <div class="shell">
      <h1>{esc(title)}</h1>
      <div class="muted">Session {esc(session_id)} · manual labels in this browser: <span data-label-count>0</span></div>
      {nav}
    </div>
  </div>
  <main class="shell">{body}</main>
  <script>{SCRIPT}</script>
</body>
</html>
"""


def nav_html(arms: list[str]) -> str:
    links = ['<a href="../index.html">Index</a>', '<a href="side_by_side.html">Side by side</a>']
    for arm in arms:
        links.append(f'<a href="{slug(arm)}.html">{esc(arm_display_label(arm))}</a>')
    links.append('<button data-export-labels>Export labels</button>')
    return '<div class="nav">' + "\n".join(links) + "</div>"


def chip(label: str, value: object, yes: bool | None = None) -> str:
    cls = "chip"
    if yes is True:
        cls += " y"
    elif yes is False:
        cls += " n"
    return f'<span class="{cls}">{esc(label)} {esc(value)}</span>'


def tweet_card(row: dict, top_k: bool) -> str:
    url = tweet_url(row)
    author = row.get("author_handle") or row.get("author_name") or "unknown"
    title = f"@{author}" if row.get("author_handle") else author
    if url:
        title = f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(title)}</a>'
    rank = row.get("rank")
    rank_text = f"rank {rank}" if rank is not None else "missing pred"
    reaction = ""
    if row.get("reaction_text"):
        reaction = f'<div class="reaction"><b>LLM:</b> {esc(row["reaction_text"])}</div>'
    chips = [
        chip("save", "y" if row.get("pred_save") else "n", bool(row.get("pred_save"))),
        chip("open", "y" if row.get("pred_open") else "n", bool(row.get("pred_open"))),
        chip("stop", "y" if row.get("pred_stop") else "n", bool(row.get("pred_stop"))),
        chip("look", fmt_num(row.get("pred_look_sec"))),
        chip("orig", row.get("original_rank")),
        chip("obs_stop", "y" if row.get("obs_stop") else "n", bool(row.get("obs_stop"))),
        chip("obs_save", "y" if row.get("obs_save") else "n", bool(row.get("obs_save"))),
    ]
    if row.get("rank") is None:
        chips.append('<span class="chip warn">no LLM row</span>')
    return f"""
<article class="tweet" data-item-id="{esc(row["item_id"])}" data-top-k="{1 if top_k else 0}">
  <div class="tweet-head">
    <div class="author">{title}</div>
    <div class="rank">{esc(rank_text)}</div>
  </div>
  <div class="text">{esc(row.get("text") or row.get("rendered_text") or "")}</div>
  {reaction}
  <div class="chips">{''.join(chips)}</div>
  <div class="labels">
    <button data-label-action="save">Save</button>
    <button data-label-action="open">Open</button>
    <button data-label-action="skip">Skip</button>
  </div>
</article>
"""


def batches(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["batch_id"], []).append(row)
    return out


def render_arm_page(output: Path, session_id: str, arm: str, rows: list[dict], arms: list[str]) -> None:
    parts = [
        '<div class="toolbar">',
        '<input class="search" data-search placeholder="Search this feed">',
        '<button data-show="top">Top 12 per batch</button>',
        '<button data-show="all">All items</button>',
        '<button data-export-labels>Export labels</button>',
        '</div>',
    ]
    for batch_id, batch_rows in batches(rows).items():
        ordered = sorted(batch_rows, key=lambda r: (r["rank"] is None, r["rank"] or 999999, r["original_rank"] or 999999))
        parts.append(
            f'<section class="batch"><div class="batch-header"><b>{esc(batch_id)}</b>'
            f'<span class="muted">{len(ordered)} items · top 12 highlighted by current ordering</span></div>'
            '<div class="feed">'
        )
        for idx, row in enumerate(ordered, start=1):
            parts.append(tweet_card(row, idx <= 12))
        parts.append("</div></section>")
    title = f"{arm_display_label(arm)} Feed"
    output.write_text(page(title, session_id, "\n".join(parts), nav_html(arms)), encoding="utf-8")


def render_side_by_side(output: Path, session_id: str, rows_by_arm: dict[str, list[dict]], arms: list[str]) -> None:
    compare_arms = [arm for arm in PREFERRED_SIDE_BY_SIDE_ARMS if arm in rows_by_arm]
    by_arm_batch = {arm: batches(rows_by_arm[arm]) for arm in compare_arms}
    batch_ids = sorted({bid for arm_batches in by_arm_batch.values() for bid in arm_batches}, key=lambda bid: "" if bid is None else str(bid))
    parts = [
        '<div class="toolbar">',
        '<input class="search" data-search placeholder="Search side-by-side cards">',
        '<button data-export-labels>Export labels</button>',
        '</div>',
    ]
    for batch_id in batch_ids:
        parts.append(
            f'<section class="batch"><div class="batch-header"><b>{esc(batch_id)}</b>'
            '<span class="muted">top 12 from each arm</span></div><div class="feed side-feed">'
        )
        for arm in compare_arms:
            rows = sorted(
                by_arm_batch[arm].get(batch_id, []),
                key=lambda r: (r["rank"] is None, r["rank"] or 999999, r["original_rank"] or 999999),
            )[:12]
            parts.append(f'<div class="column"><div class="column-title">{esc(arm_display_label(arm))}</div>')
            for row in rows:
                parts.append(tweet_card(row, True))
            parts.append("</div>")
        parts.append("</div></section>")
    output.write_text(page("Side-by-Side Top 12", session_id, "\n".join(parts), nav_html(arms)), encoding="utf-8")


def render_index(
    output: Path,
    session_id: str,
    run_dir: Path,
    scores: list[dict],
    arms: list[str],
    conn,
) -> None:
    metric_cards = []
    for score in scores:
        metric_cards.append(
            f"""
<div class="metric">
  <b>{esc(arm_display_label(score["arm"]))}</b>
  <div class="muted">save_fbeta {fmt_num(score.get("save_fbeta"))}</div>
  <div>false pulls {esc(score.get("false_pull_count"))}</div>
  <div>stop acc {fmt_num(score.get("stop_accuracy"))}</div>
  <div>NDCG@12 {fmt_num(score.get("utility_ndcg_at_12"))}</div>
</div>
"""
        )
    rows = []
    for score in scores:
        rows.append(
            "<tr>"
            f"<td>{esc(arm_display_label(score['arm']))}</td>"
            f"<td>{esc(score.get('n_exposed'))}</td>"
            f"<td>{esc(score.get('n_saves'))}</td>"
            f"<td>{fmt_num(score.get('save_fbeta'))}</td>"
            f"<td>{fmt_num(score.get('utility_ndcg_at_12'))}</td>"
            f"<td>{esc(score.get('false_pull_count'))}</td>"
            f"<td>{fmt_num(score.get('stop_accuracy'))}</td>"
            "</tr>"
        )
    feed_links = ["<ul>"]
    feed_links.append(
        '<li><a href="feeds/side_by_side.html">Side-by-side top 12: Native vs Cheap Combo vs M3</a></li>'
    )
    for arm in arms:
        feed_links.append(f'<li><a href="feeds/{slug(arm)}.html">{esc(arm_display_label(arm))} ranked feed</a></li>')
    feed_links.append("</ul>")
    body = f"""
<h2>Review Feeds</h2>
<p class="muted">These are frozen HTML views over the captured candidates. Rankings are applied within each 40-item batch.</p>
{''.join(feed_links)}
<h2>Feed run ratios</h2>
<p class="muted">Latest feed_runs per arm: coverage and curation over the x_returned_candidates window.</p>
{render_feed_run_ratio_cards(load_latest_feed_runs(conn, session_id), escape=esc)}
<h2>Current Scores</h2>
<div class="grid metrics">{''.join(metric_cards)}</div>
<h2>Metrics Table</h2>
<table>
  <thead><tr><th>arm</th><th>n</th><th>saves</th><th>save_fbeta</th><th>utility_ndcg@12</th><th>false pulls</th><th>stop acc</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<h2>Manual Review</h2>
<p class="muted">Use Save/Open/Skip on cards while reviewing. Labels stay in browser localStorage for this session and can be exported as <code>manual_labels.json</code>.</p>
"""
    output.write_text(page("Condom Experiment Feed Review", session_id, body, '<div class="nav"><a href="feeds/side_by_side.html">Open side-by-side</a><button data-export-labels>Export labels</button></div>'), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    conn = connect(db_path)
    arms = arms_for_session(conn, args.session_id)
    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        base = latest_run_dir()
        if base and (base / "metrics.json").exists():
            run_dir = base
        else:
            run_dir = RUNS / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    feed_dir = run_dir / "feeds"
    feed_dir.mkdir(parents=True, exist_ok=True)

    scores = load_scores(conn, args.session_id)
    rows_by_arm = {arm: load_rows(conn, args.session_id, arm) for arm in arms}
    for arm, rows in rows_by_arm.items():
        render_arm_page(feed_dir / f"{slug(arm)}.html", args.session_id, arm, rows, arms)
    render_side_by_side(feed_dir / "side_by_side.html", args.session_id, rows_by_arm, arms)
    render_index(run_dir / "index.html", args.session_id, run_dir, scores, arms, conn)

    print(json.dumps({
        "run_dir": str(run_dir),
        "index": str(run_dir / "index.html"),
        "side_by_side": str(feed_dir / "side_by_side.html"),
        "feeds": {arm: str(feed_dir / f"{slug(arm)}.html") for arm in arms},
    }, indent=2))


if __name__ == "__main__":
    main()

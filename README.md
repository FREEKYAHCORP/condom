# condom

Local-first Chrome MV3 extension plus Python core for capturing and re-ranking X For You items the browser already received.

## What this is

A research harness for studying how different rankers reorder the same captured timeline. While you browse X in Chrome, the extension passively copies GraphQL/API response bodies and DOM-visible posts, sends them to a local FastAPI service on `127.0.0.1:8765`, and stores everything in SQLite. You can compare three ranking modes on the same session: preserve native order, a cheap local reranker, or an optional LLM user-simulation rerank via MiniMax.

The popup lets you pick a mode; non-native modes add rank badges and highlights on visible posts. An optional experimental DOM reorder can change on-screen order without calling X APIs.

## What this is not

- Not a product that “fixes” or replaces your X feed.
- Not a wellness or sexual-health metaphor project—the name is incidental lab shorthand.
- Not an extension that holds API keys or calls X on your behalf beyond what the open tab already does.
- Not a claim about X Terms of Service compliance; treat capture as experimental instrumentation.
- Not validated save-prediction science out of the box—save/bookmark detection is best-effort and sessions with zero saves cannot support headline save metrics.

## Privacy and data boundaries

- **Local by default.** Ingest and ranking run on your machine; the database lives under `data/processed/experiment.sqlite`.
- **Extension credentials.** The MV3 extension does not store `MINIMAX_API_KEY` or any cloud API key. It only talks to `http://127.0.0.1:8765` (or `http://localhost:8765`).
- **Cloud egress (opt-in).** Only **m3** mode may send prompt packets to MiniMax when you set `MINIMAX_API_KEY` in a repo-root `.env` and select that mode. **native** and **cheap** stay on-device.
- **Behavior vs prompts.** DOM exposure and click-derived events are logged for scoring and replay; they are not shipped to the Encounter LLM prompt as live behavior telemetry.
- **Your data.** Captured timelines, profiles, and run outputs stay in `data/` and `runs/` unless you copy them elsewhere.

## Architecture

```
X tab (x.com / twitter.com)
  → page_fetch_hook.js (MAIN world: intercept fetch/XHR bodies the page already gets)
  → content_script.js (DOM scan, exposure, best-effort saves, rank UI)
  → service_worker.js (queue, flush, session/mode state)
  → POST http://127.0.0.1:8765/ingest/*
  → SQLite (data/processed/experiment.sqlite)
  → GET /rank?session_id=…&mode=native|cheap|m3
  → extension applies badges / highlights / optional experimental reorder
```

Python package **`condom_core`** (`src/condom_core/`) provides ingest parsers, rankers, prompts, and the FastAPI app (`scripts/serve_core.py`).

## Ranking modes

| UI / API mode | Offline arm name | Implementation | Cloud |
|---------------|------------------|----------------|-------|
| Native X | `native_x_order` | `native` ranker preserves captured native order | No |
| Cheap reranker | `cheap_combo_v0` | `cheap_combo` local features + profile text | No |
| MiniMax M3 | `llm_usersim_encounter` | Encounter-style user-sim prompt; model `MiniMax-M3` | MiniMax only when m3 selected and key present |

API: `GET /rank?session_id=<id>&mode=native|cheap|m3&refresh=false`.

## Data flow in a live session

1. Load the unpacked extension and open `https://x.com/home` (or `twitter.com`).
2. The page hook posts `raw-response` messages for interesting `/i/api/` and GraphQL URLs; the content script forwards them to the service worker.
3. The service worker batches `POST /ingest/raw-response`, `POST /ingest/items`, and `POST /ingest/events` every ~2s (and on tab hide flush).
4. Core parses bodies into items, rebuilds session order, and persists events (exposure, clicks, best-effort saves).
5. On an interval and when mode changes, the extension calls `GET /rank` and paints rank/score badges; optional reorder moves DOM cells when enabled in the popup.
6. Offline scripts can replay the same DB into HTML feeds and metrics under `runs/` (see below).

## Quickstart

Requires **Python 3.11+**.

```bash
cd condom
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python scripts/init_db.py
python scripts/serve_core.py
```

Core listens on **`http://127.0.0.1:8765`**. Check `GET /health`.

**Chrome:** `chrome://extensions` → Developer mode → **Load unpacked** → select the `condom/extension` directory.

Open X, use the extension popup to set session/mode. For **m3** only, copy `.env.example` to `.env` and set:

```bash
MINIMAX_API_KEY=your_key_here
```

Restart the core after changing `.env`.

## Local core API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness, DB path, whether MiniMax key is loaded |
| `POST` | `/ingest/raw-response` | Store a captured network body (session, url, body, ids) |
| `POST` | `/ingest/items` | DOM-derived item snapshots |
| `POST` | `/ingest/events` | Exposure/behavior events |
| `GET` | `/rank` | Return ranked items for `session_id` and `mode` |

CORS is open for local extension use. All ingest endpoints expect JSON bodies matching `condom_core.api.schemas`.

## Repository layout

| Path | Role |
|------|------|
| `extension/` | MV3 sensor + popup (manifest may still say “Lens M0”) |
| `src/condom_core/` | DB, ingest, rankers, prompts, FastAPI |
| `scripts/` | Thin operator entrypoints over `condom_core` (`init_db.py`, `serve_core.py`, `rank_session.py`, scoring/render) |
| `prompts/` | Identity bootstrap, Encounter user-sim, qualitative judge templates |
| `fixtures/x_graphql/` | Minimal GraphQL fixtures for tests |
| `tests/` | Parser, ranker, prompt, scoring tests |
| `data/` | `raw/`, `profile/`, `processed/` (SQLite created by init) |
| `runs/` | Generated offline HTML/metrics (not required for live capture) |

## Development and tests

```bash
cd condom
pip install -r requirements.txt
python -m pytest -q
```

`pyproject.toml` defines the `condom-core` package with `src` layout. Use `condom_core` imports from repo root after `pip install -e .` or with `pythonpath` as in pytest config.

## Offline harness

Scripts under `scripts/` replay stored sessions without the browser—for example `rank_session.py` (offline arms via `--arm` or API `--mode`), `render_feeds.py`, `score.py`, `compare_arms.py`. Ranking logic lives in `src/condom_core/` (`session_ranking.py`, rankers); scripts only parse CLI args and call core. Outputs land under `runs/` (feed HTML, judge packets, metrics). This path is optional for understanding rankers; live M0/M1 capture uses extension + core only.

Qualitative external review (e.g. pasting judge packets into a separate chat model) is explicit egress outside the core measurement loop.

## Known limitations

- **X schema and DOM churn** — GraphQL shapes and `data-testid` selectors break without warning; parsers are tolerant but not future-proof.
- **Best-effort save detection** — Bookmark/save signals infer from button labels and test ids; validate on real sessions before trusting save metrics.
- **SQLite single-writer** — Concurrent heavy offline jobs and live ingest share one DB; avoid parallel writers.
- **MV3 service worker sleep** — Background flush may lag if the worker is suspended; visibility flush mitigates but does not eliminate loss risk.
- **Experimental DOM reorder** — Reordering visible cells is fragile and does not change X’s real feed server-side.
- **m3 cost and egress** — LLM calls only when you opt into m3 with a key; failures fall back to native predictions for UI when M3 produces no rows.

Sessions with **zero saves** should not be reported as valid headline save-prediction results (`headline_valid` stays false in scoring).

## License and naming

**condom** is a local research repo name and Python package **`condom_core`** (`condom-core` on PyPI metadata in `pyproject.toml`). Extension branding may still read “Lens M0” from earlier milestones. Add a `LICENSE` file at the repo root when you publish or share the tree; this README does not substitute for a license grant.
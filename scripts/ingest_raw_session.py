from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.config import RAW
from condom_core.db import connect, init_db, insert_events, upsert_items
from condom_core.models import Item
from condom_core.parse_x import item_from_dom, parse_response


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_network_items(session_dir: Path, session_id: str) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for path in sorted((session_dir / "network").glob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in parse_response(body, session_id=session_id):
            items.setdefault(item.item_id, item)
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()

    session_dir = RAW / args.session_id
    events = read_jsonl(session_dir / "events.jsonl")
    dom_rows = read_jsonl(session_dir / "dom_items.jsonl")
    network_items = load_network_items(session_dir, args.session_id)
    dom_items = {
        item.item_id: item
        for item in (item_from_dom(row, args.session_id) for row in dom_rows)
        if item
    }

    event_order: list[str] = []
    normalized_events = []
    for ev in events:
        item_id = str(ev.get("item_id") or "")
        if not item_id:
            continue
        if item_id not in event_order:
            event_order.append(item_id)
        ev = dict(ev)
        ev.setdefault("event_id", f"{args.session_id}:{item_id}")
        ev.setdefault("session_id", args.session_id)
        ev.setdefault("exposed", 1)
        ev.setdefault("visible_ms", 0)
        ev.setdefault("stop", 0)
        ev.setdefault("save", 0)
        ev.setdefault("look_sec", (ev.get("visible_ms") or 0) / 1000)
        ev.setdefault("profile_open", 0)
        ev.setdefault("thread_open", 0)
        ev.setdefault("link_click", 0)
        ev.setdefault("exposed_surface", "x_for_you")
        ev.setdefault("ts", datetime.now(timezone.utc).isoformat())
        normalized_events.append(ev)

    final_items: list[Item] = []
    for idx, item_id in enumerate(event_order, start=1):
        item = network_items.get(item_id) or dom_items.get(item_id)
        if not item:
            continue
        item.session_id = args.session_id
        item.batch_id = f"{args.session_id}_b{(idx - 1) // 40:03d}"
        item.original_rank = idx
        final_items.append(item)

    conn = connect()
    init_db(conn)
    inserted_items = upsert_items(conn, final_items, datetime.now(timezone.utc).isoformat())
    inserted_events = insert_events(conn, normalized_events)
    print(json.dumps({
        "session_id": args.session_id,
        "network_items": len(network_items),
        "dom_items": len(dom_items),
        "event_items": len(event_order),
        "inserted_items": inserted_items,
        "inserted_events": inserted_events,
    }, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--labels", required=True, help="Path to manual_labels.json exported from the feed review HTML.")
    args = parser.parse_args()

    path = Path(args.labels)
    labels = json.loads(path.read_text(encoding="utf-8"))
    conn = connect()
    conn.execute(
        "DELETE FROM events WHERE session_id=? AND exposed_surface='manual_review'",
        (args.session_id,),
    )
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in labels:
        item_id = str(row.get("item_id") or "")
        label = row.get("label")
        if not item_id or label not in {"save", "open", "skip"}:
            continue
        exists = conn.execute(
            "SELECT 1 FROM items WHERE session_id=? AND item_id=?",
            (args.session_id, item_id),
        ).fetchone()
        if not exists:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO events (
              event_id, item_id, session_id, exposed, visible_ms, stop, save,
              look_sec, profile_open, thread_open, link_click, exposed_surface, ts
            ) VALUES (?, ?, ?, 1, 0, ?, ?, 0, 0, ?, 0, 'manual_review', ?)
            """,
            (
                f"manual:{args.session_id}:{item_id}",
                item_id,
                args.session_id,
                1 if label in {"save", "open"} else 0,
                1 if label == "save" else 0,
                1 if label == "open" else 0,
                row.get("ts") or now,
            ),
        )
        inserted += 1
    conn.commit()
    print(json.dumps({"inserted_manual_events": inserted, "labels_path": str(path)}, indent=2))


if __name__ == "__main__":
    main()

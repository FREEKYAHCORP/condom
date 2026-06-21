from __future__ import annotations

import json
from datetime import datetime, timezone

from .join import joined_rows
from .metrics import look_mae_bucketed, ndcg_at_12, open_auc, save_fbeta, stop_accuracy, topk


def score_arm(conn, arm: str, session_id: str) -> dict:
    rows = joined_rows(conn, arm, session_id)
    precision, recall, fbeta, fp, fn = save_fbeta(rows)
    p12, r12 = topk(rows, 12)
    n_saves = sum(1 for r in rows if r["obs_save"])
    headline_valid = n_saves > 0
    result = {
        "arm": arm,
        "session_id": session_id,
        "split_name": "session",
        "n_exposed": len(rows),
        "n_saves": n_saves,
        "headline_valid": headline_valid,
        "save_precision": precision,
        "save_recall": recall,
        "save_fbeta": fbeta,
        "save_precision_at_12": p12,
        "save_recall_at_12": r12,
        "utility_ndcg_at_12": ndcg_at_12(rows),
        "false_skip_saves": fn,
        "false_pull_count": fp,
        "stop_accuracy": stop_accuracy(rows),
        "open_auc": open_auc(rows),
        "look_mae_bucketed": look_mae_bucketed(rows),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO scores (
          score_id, arm, session_id, split_name, n_exposed, n_saves, headline_valid,
          save_precision, save_recall, save_fbeta, save_precision_at_12,
          save_recall_at_12, utility_ndcg_at_12, false_skip_saves,
          false_pull_count, stop_accuracy, open_auc, look_mae_bucketed, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{arm}:{session_id}:session",
            arm,
            session_id,
            "session",
            result["n_exposed"],
            result["n_saves"],
            1 if result["headline_valid"] else 0,
            result["save_precision"],
            result["save_recall"],
            result["save_fbeta"],
            result["save_precision_at_12"],
            result["save_recall_at_12"],
            result["utility_ndcg_at_12"],
            result["false_skip_saves"],
            result["false_pull_count"],
            result["stop_accuracy"],
            result["open_auc"],
            result["look_mae_bucketed"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return result


def markdown_table(results: list[dict]) -> str:
    cols = [
        "arm", "n_exposed", "n_saves", "headline_valid", "save_precision", "save_recall",
        "save_fbeta", "save_precision_at_12", "save_recall_at_12",
        "utility_ndcg_at_12", "false_skip_saves", "false_pull_count",
        "stop_accuracy", "open_auc", "look_mae_bucketed",
    ]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for result in results:
        vals = []
        for col in cols:
            val = result.get(col)
            vals.append(f"{val:.4f}" if isinstance(val, float) else ("" if val is None else str(val)))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def dump_results(path, results: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (path / "metrics.md").write_text(markdown_table(results) + "\n", encoding="utf-8")

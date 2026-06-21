from __future__ import annotations

import json
from datetime import datetime, timezone


def rows_for_session(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.*
        FROM items i
        JOIN events e ON e.item_id = i.item_id AND e.session_id = i.session_id AND e.exposed = 1
        WHERE i.session_id = ?
        GROUP BY i.item_id
        ORDER BY i.original_rank
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def batches_for_session(conn, session_id: str) -> dict[str, list[dict]]:
    batches: dict[str, list[dict]] = {}
    for row in rows_for_session(conn, session_id):
        batches.setdefault(row["batch_id"], []).append(row)
    return batches


def insert_ranked_predictions(
    conn,
    arm: str,
    session_id: str,
    batch_id: str,
    ranked: list[tuple[dict, float]],
    model_name: str | None = None,
    prompt_version: str | None = None,
    identity_version: int = 0,
) -> None:
    batch_size = max(1, len(ranked))
    created_at = datetime.now(timezone.utc).isoformat()
    for rank, (row, score) in enumerate(ranked, start=1):
        conn.execute(
            """
            INSERT OR REPLACE INTO arm_predictions (
              prediction_id, arm, item_id, session_id, batch_id, rank, score,
              pred_stop, pred_open, pred_save, pred_look_sec, reaction_text,
              receipt, identity_version, prompt_version, rendered_text, model_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{arm}:{session_id}:{batch_id}:{row['item_id']}",
                arm,
                row["item_id"],
                session_id,
                batch_id,
                rank,
                float(score) if score is not None else None,
                1 if rank <= 20 else 0,
                1 if rank <= 8 else 0,
                1 if rank <= 12 else 0,
                max(0.0, 12.0 * (1.0 - rank / batch_size)),
                None,
                json.dumps({"mapping": "rank_to_prediction_v0"}, ensure_ascii=True),
                identity_version,
                prompt_version,
                row["rendered_text"],
                model_name,
                created_at,
            ),
        )
    conn.commit()


def insert_llm_prediction(
    conn,
    arm: str,
    session_id: str,
    batch_id: str,
    row: dict,
    parsed: dict,
    rank: int,
    score: float,
    response_text: str,
    prompt_version: str,
    model_name: str,
    identity_version: int = 0,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO arm_predictions (
          prediction_id, arm, item_id, session_id, batch_id, rank, score,
          pred_stop, pred_open, pred_save, pred_look_sec, reaction_text,
          receipt, identity_version, prompt_version, rendered_text, model_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{arm}:{session_id}:{batch_id}:{row['item_id']}",
            arm,
            row["item_id"],
            session_id,
            batch_id,
            rank,
            score,
            parsed["pred_stop"],
            parsed["pred_open"],
            parsed["pred_save"],
            parsed["pred_look_sec"],
            parsed["reaction_text"],
            response_text,
            identity_version,
            prompt_version,
            row["rendered_text"],
            model_name,
            created_at,
        ),
    )
    conn.commit()

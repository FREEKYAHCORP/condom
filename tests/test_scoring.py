import sqlite3

from condom_core.db import init_db
from condom_core.scoring.compare import score_arm
from condom_core.scoring.metrics import save_fbeta


def test_save_fbeta_weights_recall():
    rows = [
        {"pred_save": 1, "obs_save": 1},
        {"pred_save": 1, "obs_save": 0},
        {"pred_save": 0, "obs_save": 1},
    ]
    precision, recall, fbeta, fp, fn = save_fbeta(rows, beta=2)
    assert precision == 0.5
    assert recall == 0.5
    assert fbeta == 0.5
    assert fp == 1
    assert fn == 1


def test_score_arm_marks_zero_save_headline_invalid():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO items (item_id, source, session_id, batch_id, original_rank, rendered_text, raw_json, harvested_at)
        VALUES ('1', 'x', 's', 'b0', 1, 'hello <1>', '{}', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO events (event_id, item_id, session_id, exposed, save, stop, look_sec, exposed_surface, ts)
        VALUES ('e1', '1', 's', 1, 0, 0, 0, 'x_for_you', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO arm_predictions (prediction_id, arm, item_id, session_id, batch_id, rank, pred_save, pred_stop, pred_open, pred_look_sec, reaction_text, rendered_text, created_at)
        VALUES ('p1', 'a', '1', 's', 'b0', 1, 0, 0, 0, 0, '', 'hello <1>', 'now')
        """
    )
    conn.commit()
    result = score_arm(conn, 'a', 's')
    assert result["n_saves"] == 0
    assert result["headline_valid"] is False

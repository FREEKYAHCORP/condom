from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from sklearn.metrics import roc_auc_score


def save_fbeta(rows: list[dict], beta: float = 2.0) -> tuple[float, float, float, int, int]:
    tp = sum(1 for r in rows if r["pred_save"] and r["obs_save"])
    fp = sum(1 for r in rows if r["pred_save"] and not r["obs_save"])
    fn = sum(1 for r in rows if (not r["pred_save"]) and r["obs_save"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    b2 = beta * beta
    fbeta = ((1 + b2) * precision * recall / (b2 * precision + recall)) if precision + recall else 0.0
    return precision, recall, fbeta, fp, fn


def topk(rows: list[dict], k: int = 12) -> tuple[float | None, float | None]:
    by_batch: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_batch[row["batch_id"]].append(row)
    precisions = []
    recall_nums = []
    recall_dens = []
    for batch_rows in by_batch.values():
        ordered = sorted(batch_rows, key=lambda r: r["rank"] or 10**9)
        top = ordered[:k]
        saves_in_top = sum(1 for r in top if r["obs_save"])
        total_saves = sum(1 for r in batch_rows if r["obs_save"])
        precisions.append(saves_in_top / k)
        if total_saves:
            recall_nums.append(saves_in_top)
            recall_dens.append(total_saves)
    precision_at_k = sum(precisions) / len(precisions) if precisions else None
    recall_at_k = sum(recall_nums) / sum(recall_dens) if recall_dens else None
    return precision_at_k, recall_at_k


def dcg(values: list[float]) -> float:
    return sum(v / math.log2(i + 2) for i, v in enumerate(values))


def ndcg_at_12(rows: list[dict]) -> float | None:
    by_batch: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_batch[row["batch_id"]].append(row)
    values = []
    for batch_rows in by_batch.values():
        ordered = sorted(batch_rows, key=lambda r: r["rank"] or 10**9)[:12]
        gains = [r["obs_utility"] for r in ordered]
        ideal = sorted([r["obs_utility"] for r in batch_rows], reverse=True)[:12]
        ideal_dcg = dcg(ideal)
        if ideal_dcg:
            values.append(dcg(gains) / ideal_dcg)
    return sum(values) / len(values) if values else None


def stop_accuracy(rows: list[dict]) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if bool(r["pred_stop"]) == bool(r["obs_stop"])) / len(rows)


def open_auc(rows: list[dict]) -> float | None:
    y = [1 if r["obs_open"] else 0 for r in rows]
    if len(set(y)) < 2:
        return None
    scores = [float(r["score"] if r["score"] is not None else r["pred_open"]) for r in rows]
    return float(roc_auc_score(y, scores))


def look_mae_bucketed(rows: list[dict]) -> float | None:
    if not rows:
        return None
    def bucket(x: float) -> int:
        if x <= 0:
            return 0
        if x <= 3:
            return 1
        if x <= 8:
            return 2
        if x <= 20:
            return 3
        return 4
    return float(np.mean([abs(bucket(r["pred_look_sec"] or 0) - bucket(r["obs_look"] or 0)) for r in rows]))

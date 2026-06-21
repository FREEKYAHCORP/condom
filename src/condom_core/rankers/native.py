from __future__ import annotations


ARM = "native_x_order"


def rank(rows: list[dict]) -> list[tuple[dict, float]]:
    ordered = sorted(rows, key=lambda r: (r["batch_id"], r["original_rank"] or 10**9))
    return [(row, float(-(row["original_rank"] or 0))) for row in ordered]

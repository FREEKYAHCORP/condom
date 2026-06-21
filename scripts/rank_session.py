from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from condom_core.db import connect
from condom_core.session_ranking import M3Options, rank_session_arm, resolve_arm


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a ranking arm for a stored session (thin wrapper over condom_core.session_ranking).",
    )
    parser.add_argument("--session-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--arm", help="Arm name, e.g. native, cheap_combo, bm25, tfidf, m3, cheap_linear")
    group.add_argument("--mode", help="API mode alias: native, cheap, m3")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Keep existing predictions when the arm already has rows (default: clear and rerun).",
    )
    parser.add_argument("--limit-batches", type=int, help="M3 only: cap batches processed")
    parser.add_argument("--skip-complete", action="store_true", help="M3 only: skip batches that already have full predictions")
    parser.add_argument("--rerun-partial", action="store_true", help="M3 only: rerun only incomplete batches")
    parser.add_argument("--strict-completeness", action="store_true", help="M3 only: append strict completeness suffix to prompt")
    parser.add_argument("--max-calls", type=int, help="M3 only: stop after this many model calls")
    args = parser.parse_args()

    arm_key = args.arm or args.mode
    arm = resolve_arm(arm_key)
    refresh = not args.no_refresh

    m3_options = None
    if arm == "llm_usersim_encounter":
        m3_options = M3Options(
            refresh=refresh and not (args.skip_complete or args.rerun_partial),
            limit_batches=args.limit_batches,
            skip_complete=args.skip_complete,
            rerun_partial=args.rerun_partial,
            strict_completeness=args.strict_completeness,
            max_calls=args.max_calls,
        )

    conn = connect()
    result = rank_session_arm(
        conn,
        args.session_id,
        arm_key,
        refresh=refresh,
        m3_options=m3_options,
    )

    if result.get("calls_made") is not None:
        print(
            f"{result['arm']}: made {result['calls_made']} calls, "
            f"wrote {result['predictions']} predictions"
        )
        for summary in result.get("model_calls") or []:
            print(
                f"  batch {summary['batch_id']}: "
                f"parsed {summary['parsed_count']}/{summary['expected_count']}"
            )
    elif result.get("status") == "skipped":
        print(f"{result['arm']}: skipped, insufficient prior labeled sessions")
    else:
        print(f"{result['arm']}: wrote {result['predictions']} predictions")


if __name__ == "__main__":
    main()
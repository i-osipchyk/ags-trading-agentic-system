"""CLI: run the aligned pipeline (the challenger) over past official runs, to score it against them.

Each official run from --since, at the scheduled cycle times, is rerun on its frozen inputs (ags/frozen.py) with the
aligned prompts and analyst envelope (ROADMAP.md Step 1). The reruns go to replays/analysts/<batch>/logs/, never the
official record; the Scorecard page and `ags.scoring.compare_challenger` read them from there. It stops and resumes
cleanly: a run that already has its log is skipped.

By default it only prints the plan, with a rough cost and time. Add --run to spend the model calls.

Usage: uv run python scripts/run_challenger.py [--since 2026-10-13] [--until DATE] [--slots 06:00,12:30]
           [--commodities corn,wheat] [--batch aligned-v1] [--workers 4] [--limit N] [--run]
Run `scripts/sync_local.sh logs data` and the Dashboard's pull first, so logs and daily closes are current.
"""

import argparse
from datetime import date
from pathlib import Path

from ags import blockers, challenger, scoring
from ags.agents.auditor import _triggered_at
from ags.config import Config
from ags.frozen import FrozenInputs
from ags.llm.client import LLMClient
from ags.pipeline import run_pipeline
from ags.storage import LocalStorage

_ROOT = Path(__file__).resolve().parents[1]  # repo root


def _run_one(config: Config):
    official = LocalStorage(_ROOT / "logs")

    def run_one(run_id: str, frozen: FrozenInputs, store) -> None:
        scheduled = _triggered_at(run_id)
        client = LLMClient()
        try:
            run_pipeline(
                commodity=run_id.rsplit("_", 1)[0], as_of=scheduled, trigger_timestamp=scheduled, chat_client=client,
                model=config.deepseek_model, log_dir=store, data_dir=_ROOT / "data", audit_dir=_ROOT / "audits",
                aligned=True, frozen=frozen, track_record_dir=official,
            )
        finally:
            client.flush()

    return run_one


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the aligned pipeline over past official runs.")
    parser.add_argument("--since", type=date.fromisoformat, default=scoring.REVIEW_START)
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--slots", default=",".join(challenger.REVIEW_SLOTS),
        help="ET cycle times; the default covers both schedules since 2026-10-13. e.g. 06:00,12:30 for the current one",
    )
    parser.add_argument("--commodities", default=None, help="comma-separated; default all")
    parser.add_argument("--batch", default="aligned-v1", help="folder name under replays/analysts/")
    parser.add_argument("--workers", type=int, default=4, help="commodities run in parallel")
    parser.add_argument("--limit", type=int, default=None, help="only the first N runs (a pilot)")
    parser.add_argument("--run", action="store_true", help="make the model calls (default: only print the plan)")
    args = parser.parse_args(argv)

    logs = _ROOT / "logs"
    run_ids = challenger.select_runs(
        logs, since=args.since, until=args.until, slots=tuple(args.slots.split(",")),
        commodities=tuple(args.commodities.split(",")) if args.commodities else None,
    )
    if args.limit is not None:
        run_ids = run_ids[: args.limit]
    out = _ROOT / "replays" / "analysts" / args.batch
    plan = challenger.estimate(logs, run_ids, workers=args.workers)
    print(f"{plan['runs']} official runs {run_ids[0] if run_ids else ''} .. {run_ids[-1] if run_ids else ''} -> {out}")
    print(f"rough cost ${plan['cost_usd']:.2f}, about {plan['hours']:.1f} h with {args.workers} workers")
    if not args.run:
        print("plan only; add --run to make the model calls")
        return

    try:
        challenger.check_batch(out)
    except challenger.BatchChanged as exc:
        raise SystemExit(str(exc))
    blockers.clear()
    outcome = challenger.run_challenger(
        run_ids, official_logs=logs, out=out, run_one=_run_one(Config.from_env()), workers=args.workers,
        blocked=blockers.seen,
    )
    print(f"\ndone {len(outcome.done)}, skipped {len(outcome.skipped)}, failed {len(outcome.failed)}")
    if outcome.stopped:
        print(f"STOPPED: {outcome.stopped}")
    if outcome.failed:
        print(f"failures are in {out / 'failures.jsonl'}; rerun the same command to retry them")


if __name__ == "__main__":
    main()

"""CLI: retry a held cycle once whatever blocked it (no credit, a rejected key) is fixed.

A cycle that hits a blocking provider error is held: logged, alerted, not written to either
book (ARCHITECTURE.md § Retrying a held cycle). This starts the cycle state machine again with
the held slot's own scheduled_time, so the retry has the same cycle_id and run ids: commodities
whose call wasn't blocked are reused as made on time, the rest are re-run (their first attempt
archived under attempts/), and the portfolio stage writes the slot to both books. It is an
official cycle.

The Lambdas enforce the window (RETRY_WINDOW_HOURS, default 3 h after the slot). This script
checks the same things first, so a retry that would abort spends nothing: the slot was
attempted, isn't in the book yet, no cycle is running, and the window is still open.

Usage: uv run python scripts/retry_cycle.py [--env live] [--slot 2026-10-26T20:00:00Z]
       (default slot: the latest 06:00/12:30 ET weekday slot at or before now)
"""

import argparse
import json
import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PROJECT = "ags"
ET = ZoneInfo("America/New_York")
# The cycle schedules, in New York time, latest first: cron(0 6 ? * MON-FRI *) and cron(30 12 ? * MON-FRI *).
SLOTS = ((12, 30), (6, 0))
SCHEDULE = "cycle-morning"  # either schedule names the same state machine


def latest_slot(now: datetime) -> datetime:
    """The latest scheduled slot at or before `now`, in UTC."""
    local = now.astimezone(ET)
    for days in range(5):  # Monday 05:00 ET reaches back to Friday 12:30
        day = (local - timedelta(days=days)).date()
        if day.weekday() >= 5:
            continue
        for hour, minute in SLOTS:
            slot = datetime.combine(day, time(hour, minute), tzinfo=ET)
            if slot <= local:
                return slot.astimezone(timezone.utc)
    raise ValueError(f"no cycle slot in the 5 days before {now}")


def scheduled_time(slot: datetime) -> str:
    """The slot as Scheduler writes <aws.scheduler.scheduled-time>, so the ids match exactly."""
    return slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None, *, scheduler=None, lambda_=None, sfn=None, storage=None,
         now: datetime | None = None) -> None:
    from ags.handlers.runtime import cycle_id_for, retry_window_from

    parser = argparse.ArgumentParser(description="Retry a held cycle.")
    parser.add_argument("--env", default="live")
    parser.add_argument("--slot", help="the held slot's scheduled time, ISO (default: the latest slot)")
    args = parser.parse_args(argv)

    if scheduler is None:
        import boto3

        os.environ.setdefault("AWS_REGION", "eu-west-1")
        scheduler, lambda_, sfn = boto3.client("scheduler"), boto3.client("lambda"), boto3.client("stepfunctions")
    now = now or datetime.now(timezone.utc)
    slot = datetime.fromisoformat(args.slot) if args.slot else latest_slot(now)
    when = scheduled_time(slot)
    cycle_id = cycle_id_for(when)
    group = f"{PROJECT}-{args.env}"

    env = lambda_.get_function_configuration(FunctionName=f"{group}-portfolio")["Environment"]["Variables"]
    if storage is None:
        from ags.storage import S3Storage

        storage = S3Storage(env["AGS_BUCKET"])
    window = retry_window_from(env)

    from ags.portfolio.stores import manager_store

    if storage.get(f"portfolio/{manager_store(env.get('MANAGER_BOOK', 'paper'))}/cycles/{cycle_id}.json") is not None:
        raise SystemExit(f"{cycle_id} is already in the book; nothing to retry")
    if storage.get(f"portfolio/logs/{cycle_id}.jsonl") is None:
        raise SystemExit(
            f"{cycle_id} was never attempted, so there's nothing held to retry. "
            "scripts/trigger_cycle.py starts a new cycle now instead."
        )
    if now - slot > window:
        raise SystemExit(f"{cycle_id}: the {window} retry window closed at {slot + window:%Y-%m-%d %H:%M} UTC")
    arn = scheduler.get_schedule(GroupName=group, Name=SCHEDULE)["Target"]["Arn"]
    if sfn.list_executions(stateMachineArn=arn, statusFilter="RUNNING", maxResults=1)["executions"]:
        raise SystemExit("a cycle is still running; retry once it has finished")

    name = f"retry-{cycle_id}-{now:%H%M%S}"
    payload = json.dumps({"scheduled_time": when})
    sfn.start_execution(stateMachineArn=arn, name=name, input=payload)
    print(f"started {name} on {arn}")
    print(f"input: {payload}; must start by {slot + window:%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()

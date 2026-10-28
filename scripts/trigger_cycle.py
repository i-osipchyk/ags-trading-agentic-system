"""CLI: fire one cycle on AWS now, through exactly the path the 06:00/12:30 ET cycles take.

Creates a one-time EventBridge Scheduler schedule a couple of minutes ahead, in the same group,
with the deployed `cycle-morning` schedule's target copied verbatim: same state machine, role, retry
policy and input, so Scheduler fills in <aws.scheduler.scheduled-time> itself and the run is
indistinguishable from a scheduled one. It is an official cycle: it writes both books, trades the
manager's orders when `MANAGER_BOOK=demo`, and can alert. The schedule deletes itself after it fires.

Usage: uv run python scripts/trigger_cycle.py [--env live] [--in-minutes 2]
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

PROJECT = "ags"
PLACEHOLDER = "<aws.scheduler.scheduled-time>"
SCHEDULE = "cycle-morning"  # either cycle schedule carries the same target


def one_time_schedule(cycle: dict, fire_at: datetime) -> dict:
    """The create_schedule request: the cycle schedule's target, fired once at `fire_at` (UTC)."""
    target = {k: cycle["Target"][k] for k in ("Arn", "RoleArn", "Input", "RetryPolicy") if k in cycle["Target"]}
    return {
        "Name": "cycle-once-" + fire_at.strftime("%Y%m%dT%H%M"),
        "GroupName": cycle["GroupName"],
        "ScheduleExpression": f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": target,
        "ActionAfterCompletion": "DELETE",
        "State": "ENABLED",
    }


def fire_time(now: datetime, in_minutes: int) -> datetime:
    """`in_minutes` ahead, on a whole minute (at() has minute precision and must be in the future)."""
    return (now + timedelta(minutes=in_minutes)).replace(second=0, microsecond=0)


def main(argv: list[str] | None = None, *, scheduler=None, now: datetime | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fire one cycle through EventBridge Scheduler.")
    parser.add_argument("--env", default="live")
    parser.add_argument("--in-minutes", type=int, default=2, help="minutes from now to fire (>= 1)")
    args = parser.parse_args(argv)
    if args.in_minutes < 1:
        parser.error("--in-minutes must be at least 1")

    if scheduler is None:
        import boto3

        os.environ.setdefault("AWS_REGION", "eu-west-1")
        scheduler = boto3.client("scheduler")
    group = f"{PROJECT}-{args.env}"
    cycle = scheduler.get_schedule(GroupName=group, Name=SCHEDULE)
    if PLACEHOLDER not in cycle["Target"]["Input"]:
        raise SystemExit(f"the deployed cycle input has no literal {PLACEHOLDER}: {cycle['Target']['Input']}")
    fire_at = fire_time(now or datetime.now(timezone.utc), args.in_minutes)
    request = one_time_schedule(cycle, fire_at)
    scheduler.create_schedule(**request)
    print(f"{group}/{request['Name']} fires at {fire_at.isoformat()} -> cycle_{fire_at:%Y-%m-%dT%H-%M-%SZ}")
    print(f"input: {request['Target']['Input']}")


if __name__ == "__main__":
    main()

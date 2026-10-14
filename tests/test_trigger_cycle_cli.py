from datetime import datetime, timezone

import pytest

from scripts import trigger_cycle

INPUT = '{"scheduled_time": "<aws.scheduler.scheduled-time>", "off_schedule": false}'
CYCLE = {
    "Name": "cycle", "GroupName": "ags-live",
    "ScheduleExpression": "cron(0 4,16 ? * MON-FRI *)", "ScheduleExpressionTimezone": "America/New_York",
    "Target": {
        "Arn": "arn:aws:states:eu-west-1:1:stateMachine:ags-live-cycle",
        "RoleArn": "arn:aws:iam::1:role/ags-live-cycle-scheduler",
        "Input": INPUT,
        "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2},
    },
}
NOW = datetime(2026, 10, 14, 9, 30, 41, tzinfo=timezone.utc)


class FakeScheduler:
    def __init__(self, cycle):
        self.cycle, self.created = cycle, []

    def get_schedule(self, *, GroupName, Name):
        assert (GroupName, Name) == ("ags-live", "cycle")
        return self.cycle

    def create_schedule(self, **request):
        self.created.append(request)


def test_fires_once_through_scheduler_with_the_cycle_schedules_target_verbatim(capsys):
    scheduler = FakeScheduler(CYCLE)

    trigger_cycle.main([], scheduler=scheduler, now=NOW)

    [request] = scheduler.created
    # Same target and input as the scheduled cycle: Scheduler substitutes the placeholder, and
    # off_schedule stays false, so the run writes the books like any 04:00/16:00 cycle.
    assert request["Target"] == CYCLE["Target"]
    assert request["GroupName"] == "ags-live"
    assert request["ScheduleExpression"] == "at(2026-10-14T09:32:00)"
    assert request["ScheduleExpressionTimezone"] == "UTC"
    assert request["ActionAfterCompletion"] == "DELETE"
    assert request["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert "cycle_2026-10-14T09-32-00Z" in capsys.readouterr().out


def test_refuses_while_the_deployed_input_has_the_escaped_placeholder():
    escaped = {**CYCLE, "Target": {**CYCLE["Target"], "Input": INPUT.replace("<", "\\u003c").replace(">", "\\u003e")}}
    scheduler = FakeScheduler(escaped)

    with pytest.raises(SystemExit, match="no literal"):
        trigger_cycle.main([], scheduler=scheduler, now=NOW)
    assert scheduler.created == []


def test_rejects_firing_in_under_a_minute():
    with pytest.raises(SystemExit):
        trigger_cycle.main(["--in-minutes", "0"], scheduler=FakeScheduler(CYCLE), now=NOW)

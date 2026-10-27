import json
from datetime import datetime, timezone

import pytest

from ags.storage import LocalStorage
from scripts import retry_cycle

ARN = "arn:aws:states:eu-west-1:1:stateMachine:ags-live-cycle"
CYCLE_ID = "cycle_2026-10-26T20-00-00Z"  # Mon 16:00 ET (EDT)
SLOT = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)


class FakeScheduler:
    def get_schedule(self, *, GroupName, Name):
        assert (GroupName, Name) == ("ags-live", "cycle")
        return {"Target": {"Arn": ARN}}


class FakeLambda:
    def __init__(self, env=None):
        self.env = env or {"AGS_BUCKET": "b"}

    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "ags-live-portfolio"
        return {"Environment": {"Variables": self.env}}


class FakeSfn:
    def __init__(self, running=()):
        self.running, self.started = list(running), []

    def list_executions(self, **kwargs):
        assert kwargs["statusFilter"] == "RUNNING"
        return {"executions": self.running}

    def start_execution(self, **kwargs):
        self.started.append(kwargs)


def _held(tmp_path, recorded=False):
    storage = LocalStorage(tmp_path)
    storage.put(f"portfolio/logs/{CYCLE_ID}.jsonl", b"{}\n")
    if recorded:
        storage.put(f"portfolio/manager/cycles/{CYCLE_ID}.json", b"{}")
    return storage


def _run(storage, now, sfn=None, argv=(), env=None):
    sfn = sfn or FakeSfn()
    retry_cycle.main(list(argv), scheduler=FakeScheduler(), lambda_=FakeLambda(env), sfn=sfn, storage=storage, now=now)
    return sfn


@pytest.mark.parametrize("now, slot", [
    (datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc), "2026-10-26T20:00:00Z"),   # Mon 18:00 ET
    (datetime(2026, 10, 27, 7, 59, tzinfo=timezone.utc), "2026-10-26T20:00:00Z"),   # Tue 03:59 ET -> Mon 16:00
    (datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc), "2026-10-27T08:00:00Z"),    # Tue 04:00 ET exactly
    (datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc), "2026-10-23T20:00:00Z"),    # Mon 03:00 ET -> Fri 16:00
    (datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc), "2026-11-03T09:00:00Z"),    # after the DST change: 04:00 EST
])
def test_latest_slot_follows_the_new_york_weekday_schedule(now, slot):
    assert retry_cycle.scheduled_time(retry_cycle.latest_slot(now)) == slot


def test_retries_the_held_slot_with_its_own_scheduled_time(tmp_path):
    sfn = _run(_held(tmp_path), SLOT.replace(hour=22, minute=30))

    [started] = sfn.started
    assert started["stateMachineArn"] == ARN
    assert started["name"] == f"retry-{CYCLE_ID}-223000"
    assert json.loads(started["input"]) == {"scheduled_time": "2026-10-26T20:00:00Z", "off_schedule": False}


@pytest.mark.parametrize("setup, message", [
    (lambda p: _held(p, recorded=True), "already in the book"),
    (lambda p: LocalStorage(p), "never attempted"),
])
def test_refuses_a_slot_that_isnt_held(tmp_path, setup, message):
    with pytest.raises(SystemExit, match=message):
        _run(setup(tmp_path), SLOT.replace(hour=21))


def test_refuses_after_the_deployed_window_and_while_a_cycle_runs(tmp_path):
    storage = _held(tmp_path)
    with pytest.raises(SystemExit, match="window closed"):
        _run(storage, SLOT.replace(hour=23, minute=1))
    # The deployed RETRY_WINDOW_HOURS is the one that counts.
    _run(storage, SLOT.replace(hour=23, minute=1), env={"AGS_BUCKET": "b", "RETRY_WINDOW_HOURS": "4"})
    with pytest.raises(SystemExit, match="still running"):
        _run(storage, SLOT.replace(hour=21), sfn=FakeSfn(running=[{"name": "x"}]))


def test_an_explicit_slot_is_used_as_given(tmp_path):
    sfn = _run(_held(tmp_path), SLOT.replace(hour=21), argv=["--slot", "2026-10-26T16:00:00-04:00"])
    assert json.loads(sfn.started[0]["input"])["scheduled_time"] == "2026-10-26T20:00:00Z"

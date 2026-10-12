from datetime import datetime, timezone

import pytest

from ags.handlers.portfolio import handle
from ags.handlers.runtime import LateStartError, Runtime
from ags.storage import LocalStorage
from tests.portfolio.test_stage import CONFIG, COMMODITIES, FakeMarket, StubManager

SCHEDULED = datetime(2026, 1, 6, 21, 0, tzinfo=timezone.utc)  # Tue 16:00 ET
CYCLE_ID = "cycle_2026-01-06T21-00-00Z"


def _runtime(storage, now=SCHEDULED, manager=None):
    return Runtime(
        config=None, storage=storage, chat_client=manager or StubManager({"corn": 2.0}), model="stub",
        market_client=FakeMarket(), now=lambda: now, portfolio_config=CONFIG,
    )


def _results(**overrides):
    ok = {c: {"commodity": c, "failed": False, "call": {"call": "neutral", "conviction": 3, "thesis": "t", "key_drivers": []}}
          for c in COMMODITIES}
    for c, v in overrides.items():
        ok[c] = v
    return list(ok.values())


def _event(results, **extra):
    return {"cycle_id": CYCLE_ID, "scheduled_time": SCHEDULED.isoformat(), "off_schedule": False, "results": results, **extra}


def test_failed_commodity_becomes_a_null_call_that_the_guardrails_hold(tmp_path):
    storage = LocalStorage(tmp_path)
    corn = {"commodity": "corn", "failed": False, "call": {"call": "bullish", "conviction": 5, "thesis": "t", "key_drivers": []}}
    cocoa = {"commodity": "cocoa", "failed": True, "call": None}

    manager = StubManager({"corn": 2.0, "cocoa": 3.0})

    summary = handle(_event(_results(corn=corn, cocoa=cocoa)), _runtime(storage, manager=manager))

    assert summary["cycle_id"] == CYCLE_ID
    assert summary["failed_commodities"] == ["cocoa"]
    import json
    mgr = json.loads(storage.get(f"portfolio/manager/cycles/{CYCLE_ID}.json"))
    assert "missing_input" in [v["rule"] for v in mgr["violations"] if v["symbol"] == "cocoa"]
    assert mgr["applied_deltas"]["corn"] == pytest.approx(2.0)


def test_off_schedule_execution_writes_no_cycle_files(tmp_path):
    storage = LocalStorage(tmp_path)

    handle(_event(_results(), off_schedule=True), _runtime(storage))

    assert storage.list("portfolio/manager/cycles/") == []
    assert storage.list("portfolio/shadow/cycles/") == []


def test_late_portfolio_stage_aborts(tmp_path):
    late = datetime(2026, 1, 6, 22, 30, tzinfo=timezone.utc)

    with pytest.raises(LateStartError):
        handle(_event(_results()), _runtime(LocalStorage(tmp_path), now=late))


def _scheduled_run(storage, when, cycle_id, alerts, results, config=None):
    runtime = _runtime(storage, now=when)
    runtime.alert = alerts.append
    if config:
        runtime.portfolio_config = config
    event = {"cycle_id": cycle_id, "scheduled_time": when.isoformat(), "off_schedule": False, "results": results}
    return handle(event, runtime)


def test_a_commodity_failing_two_scheduled_cycles_running_raises_one_alert(tmp_path):
    storage, alerts = LocalStorage(tmp_path), []
    cocoa = {"commodity": "cocoa", "failed": True, "call": None}
    second = datetime(2026, 1, 7, 9, 0, tzinfo=timezone.utc)

    _scheduled_run(storage, SCHEDULED, CYCLE_ID, alerts, _results(cocoa=cocoa))
    assert alerts == []

    _scheduled_run(storage, second, "cycle_2026-01-07T09-00-00Z", alerts, _results(cocoa=cocoa))
    assert len(alerts) == 1 and "cocoa" in alerts[0]


def test_tripping_the_drawdown_breaker_raises_an_alert(tmp_path):
    import dataclasses

    alerts = []
    always_tripped = dataclasses.replace(CONFIG, drawdown_breaker=1.01)

    _scheduled_run(LocalStorage(tmp_path), SCHEDULED, CYCLE_ID, alerts, _results(), config=always_tripped)

    assert any("breaker" in a.lower() for a in alerts)


def test_off_schedule_cycles_never_alert(tmp_path):
    import dataclasses

    runtime = _runtime(LocalStorage(tmp_path))
    runtime.alert = (alerts := []).append
    runtime.portfolio_config = dataclasses.replace(CONFIG, drawdown_breaker=1.01)

    handle(_event(_results(), off_schedule=True), runtime)

    assert alerts == []


def test_cycle_id_is_derived_from_scheduled_time_when_the_event_has_none(tmp_path):
    storage = LocalStorage(tmp_path)
    event = {"scheduled_time": "2026-01-06T21:00:00Z", "off_schedule": False, "results": _results()}

    summary = handle(event, _runtime(storage))

    assert summary["cycle_id"] == "cycle_2026-01-06T21-00-00Z"
    assert storage.list("portfolio/manager/cycles/") == ["portfolio/manager/cycles/cycle_2026-01-06T21-00-00Z.json"]

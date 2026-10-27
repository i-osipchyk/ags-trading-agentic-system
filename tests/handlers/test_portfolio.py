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


def test_a_manager_that_fails_every_attempt_raises_an_alert(tmp_path):
    from tests.portfolio.test_stage import FailingManager

    runtime = _runtime(LocalStorage(tmp_path), manager=FailingManager())
    runtime.alert = (alerts := []).append

    handle(_event(_results()), runtime)

    assert len(alerts) == 1
    assert "manager" in alerts[0].lower() and "ConnectionError: provider down" in alerts[0]


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


class BrokeManager:
    """Fails like DeepSeek with no balance left, through the real blocking-error seam."""

    def complete(self, messages, tools, *, model):
        from ags.llm.client import record_if_blocking

        exc = type("OutOfBalance", (Exception,), {"status_code": 402})("Insufficient Balance")
        record_if_blocking(exc)
        raise exc


def _blocked(commodity):
    return {"commodity": commodity, "failed": True, "call": None,
            "blocked": {"deepseek": "HTTP 402: insufficient balance"}}


def test_a_blocked_cycle_is_held_out_of_both_books_with_one_alert(tmp_path):
    storage = LocalStorage(tmp_path)
    runtime = _runtime(storage, manager=BrokeManager())
    runtime.alert = (alerts := []).append

    summary = handle(_event(_results(corn=_blocked("corn"), wheat=_blocked("wheat"))), runtime)

    assert summary["held"] is True
    assert storage.list("portfolio/manager/cycles/") == [] and storage.list("portfolio/shadow/cycles/") == []
    assert len(alerts) == 1
    assert "held" in alerts[0] and "DeepSeek: HTTP 402: insufficient balance (corn, manager, wheat)" in alerts[0]
    assert "before Tue 19:00 ET" in alerts[0] and "retry_cycle.py" in alerts[0]


def test_a_tavily_block_alone_also_holds_the_slot(tmp_path):
    storage = LocalStorage(tmp_path)
    news_blocked = {"commodity": "corn", "failed": False, "call": {"call": "bullish", "conviction": 5, "thesis": "t",
                    "key_drivers": []}, "blocked": {"tavily": "ForbiddenError: usage limit"}}
    runtime = _runtime(storage)
    runtime.alert = (alerts := []).append

    assert handle(_event(_results(corn=news_blocked)), runtime)["held"] is True
    assert "Tavily: ForbiddenError: usage limit (corn)" in alerts[0]


def test_a_retry_within_the_window_writes_the_held_slot_and_archives_the_first_attempt(tmp_path):
    from ags import blockers

    storage = LocalStorage(tmp_path)
    handle(_event(_results(corn=_blocked("corn"))), _runtime(storage, manager=BrokeManager()))
    first_log = storage.get(f"portfolio/logs/{CYCLE_ID}.jsonl")

    blockers.clear()  # a new invocation
    later = datetime(2026, 1, 6, 23, 30, tzinfo=timezone.utc)  # 2.5 h after the slot
    summary = handle(_event(_results()), _runtime(storage, now=later))

    assert summary["held"] is False
    assert storage.list("portfolio/manager/cycles/") == [f"portfolio/manager/cycles/{CYCLE_ID}.json"]
    assert storage.get(f"attempts/portfolio/logs/{CYCLE_ID}.attempt1.jsonl") == first_log


def test_a_retry_after_the_window_aborts(tmp_path):
    storage = LocalStorage(tmp_path)
    handle(_event(_results(corn=_blocked("corn"))), _runtime(storage))

    with pytest.raises(LateStartError, match="retry"):
        handle(_event(_results()), _runtime(storage, now=datetime(2026, 1, 7, 0, 0, 1, tzinfo=timezone.utc)))


def test_a_retry_of_a_recorded_slot_does_nothing(tmp_path):
    storage = LocalStorage(tmp_path)
    handle(_event(_results()), _runtime(storage))
    recorded = storage.get(f"portfolio/manager/cycles/{CYCLE_ID}.json")

    summary = handle(_event(_results()), _runtime(storage, manager=BrokeManager()))

    assert summary == {"cycle_id": CYCLE_ID, "already_recorded": True}
    assert storage.get(f"portfolio/manager/cycles/{CYCLE_ID}.json") == recorded


def test_low_credit_alerts_after_a_scheduled_cycle(tmp_path):
    storage = LocalStorage(tmp_path)
    runtime = _runtime(storage)
    runtime.alert = (alerts := []).append
    runtime.balances = lambda: {"deepseek": {"available": False, "currency": "USD", "balance": 0.0},
                                "tavily": {"remaining": 0}}

    handle(_event(_results()), runtime)

    assert any(a.startswith("⛔ DeepSeek") for a in alerts) and any(a.startswith("⛔ Tavily") for a in alerts)


def test_a_failing_credit_check_never_fails_the_cycle(tmp_path):
    storage = LocalStorage(tmp_path)
    runtime = _runtime(storage)

    def boom():
        raise RuntimeError("network")

    runtime.balances = boom

    assert handle(_event(_results()), runtime)["held"] is False
    assert storage.list("portfolio/manager/cycles/") == [f"portfolio/manager/cycles/{CYCLE_ID}.json"]

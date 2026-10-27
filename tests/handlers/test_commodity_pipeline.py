import json
from datetime import datetime, timezone

import pytest

from ags.handlers.commodity_pipeline import handle
from ags.handlers.runtime import LateStartError, Runtime
from ags.storage import LocalStorage

SCHEDULED = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)  # 16:00 ET


class ExplodingChatClient:
    def complete(self, messages, tools, *, model):
        raise AssertionError("the model must not be called")


def _runtime(tmp_path, now, chat_client=None):
    return Runtime(
        config=None, storage=LocalStorage(tmp_path), chat_client=chat_client or ExplodingChatClient(),
        model="stub", market_client=None, now=lambda: now,
    )


def _event(**overrides):
    return {"commodity": "corn", "cycle_id": "cycle_2026-06-05T20-00-00Z",
            "scheduled_time": SCHEDULED.isoformat(), "off_schedule": False, **overrides}


def test_execution_starting_over_an_hour_late_aborts_without_running(tmp_path):
    late = datetime(2026, 6, 5, 21, 0, 1, tzinfo=timezone.utc)

    with pytest.raises(LateStartError):
        handle(_event(), _runtime(tmp_path, late))

    assert not list(tmp_path.rglob("*.jsonl"))


def test_on_time_run_returns_the_call_and_logs_to_storage(tmp_path):
    from tests.test_pipeline import EmptyNewsClient, FailingPricesClient, RoutingChatClient

    runtime = _runtime(tmp_path, SCHEDULED, RoutingChatClient())
    runtime.market_client = FailingPricesClient()
    runtime.news_client = EmptyNewsClient()

    result = handle(_event(), runtime)

    assert result["commodity"] == "corn"
    assert result["failed"] is False
    assert result["call"]["call"] == "neutral"
    assert any(k.startswith("logs/corn_2026-06-05T20-00-00Z") for k in runtime.storage.list("logs/"))


def test_commodity_whose_coordinator_fails_is_passed_through_as_null(tmp_path):
    from tests.test_pipeline import _CANNED, EmptyNewsClient, FailingPricesClient, RoutingChatClient

    replies = {**_CANNED, "Coordinator": {"thesis": "schema-invalid: no call field"}}
    runtime = _runtime(tmp_path, SCHEDULED, RoutingChatClient(replies))
    runtime.market_client = FailingPricesClient()
    runtime.news_client = EmptyNewsClient()

    result = handle(_event(), runtime)

    assert result == {"commodity": "corn", "failed": True, "call": None, "blocked": {}}


class OutOfBalance(Exception):
    status_code = 402


class BrokeChatClient:
    """Every call fails the way DeepSeek does with no balance left (through the real seam)."""

    def complete(self, messages, tools, *, model):
        from ags.llm.client import record_if_blocking

        exc = OutOfBalance("Error code: 402 - Insufficient Balance")
        record_if_blocking(exc)
        raise exc


def _broke_runtime(tmp_path, now=SCHEDULED, chat_client=None):
    from tests.test_pipeline import EmptyNewsClient, FailingPricesClient

    runtime = _runtime(tmp_path, now, chat_client or BrokeChatClient())
    runtime.market_client = FailingPricesClient()
    runtime.news_client = EmptyNewsClient()
    return runtime


def test_a_provider_out_of_credit_is_reported_as_blocked_and_marked_in_the_log(tmp_path):
    runtime = _broke_runtime(tmp_path)

    result = handle(_event(), runtime)

    assert result["failed"] is True
    assert result["blocked"] == {"deepseek": "HTTP 402: insufficient balance"}
    events = [json.loads(line) for line in runtime.storage.get("logs/corn_2026-06-05T20-00-00Z.jsonl").splitlines()]
    assert events[-1]["event_type"] == "blocked"


def test_a_retry_reuses_an_unblocked_call_without_calling_the_model(tmp_path):
    from tests.test_pipeline import RoutingChatClient

    runtime = _broke_runtime(tmp_path, chat_client=RoutingChatClient())
    first = handle(_event(), runtime)
    assert first["call"]["call"] == "neutral"

    runtime.chat_client = ExplodingChatClient()
    runtime.now = lambda: datetime(2026, 6, 5, 22, 30, tzinfo=timezone.utc)  # 2.5 h late
    again = handle(_event(), runtime)

    assert again["reused"] is True and again["call"] == first["call"]
    assert runtime.storage.list("attempts/") == []


def test_a_retry_archives_the_blocked_attempt_and_replaces_its_log(tmp_path):
    from tests.test_pipeline import RoutingChatClient

    runtime = _broke_runtime(tmp_path)
    handle(_event(), runtime)
    key = "logs/corn_2026-06-05T20-00-00Z.jsonl"
    blocked_log = runtime.storage.get(key)

    from ags import blockers

    blockers.clear()  # a new invocation (entry.py does this)
    runtime.chat_client = RoutingChatClient()
    runtime.now = lambda: datetime(2026, 6, 5, 22, 30, tzinfo=timezone.utc)
    result = handle(_event(), runtime)

    assert result["failed"] is False and result["blocked"] == {}
    assert runtime.storage.get("attempts/logs/corn_2026-06-05T20-00-00Z.attempt1.jsonl") == blocked_log
    events = [json.loads(line) for line in runtime.storage.get(key).splitlines()]
    assert events[0]["seq"] == 0 and not any(e["event_type"] == "blocked" for e in events)


def test_a_retry_after_the_window_aborts(tmp_path):
    runtime = _broke_runtime(tmp_path)
    handle(_event(), runtime)

    runtime.now = lambda: datetime(2026, 6, 5, 23, 0, 1, tzinfo=timezone.utc)  # 3 h 1 s late
    with pytest.raises(LateStartError, match="retry"):
        handle(_event(), runtime)


def test_an_off_schedule_run_never_takes_the_retry_path(tmp_path):
    runtime = _broke_runtime(tmp_path)
    handle(_event(), runtime)

    runtime.now = lambda: datetime(2026, 6, 5, 22, 30, tzinfo=timezone.utc)
    with pytest.raises(LateStartError, match="start"):
        handle(_event(off_schedule=True), runtime)
    assert runtime.storage.list("attempts/") == []

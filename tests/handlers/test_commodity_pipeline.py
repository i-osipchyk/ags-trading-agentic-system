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

    assert result == {"commodity": "corn", "failed": True, "call": None}

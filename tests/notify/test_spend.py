import json
from datetime import datetime, timezone

import pytest

from ags.notify.spend import llm_spend
from ags.storage import LocalStorage

NOW = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)


def _response(agent, timestamp, cost):
    # cost_usd is what llmkit priced the call at; None = model missing from its price table.
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": cost}
    return json.dumps({"seq": 0, "agent": agent, "event_type": "response", "timestamp": timestamp,
                       "payload": {"content": "x", "usage": usage}})


def _log(storage, key, *lines):
    storage.put(key, ("\n".join(lines) + "\n").encode())


def test_spend_is_summed_by_agent_and_commodity_for_24h_and_month_to_date(tmp_path):
    storage = LocalStorage(tmp_path)
    _log(storage, "logs/corn_2026-06-10T08-00-00Z.jsonl",
         _response("technical", "2026-06-10T08:00:05+00:00", 3.1),
         _response("coordinator", "2026-06-10T08:01:00+00:00", 0.5),
         json.dumps({"seq": 2, "agent": "technical", "event_type": "output", "timestamp": "2026-06-10T08:02:00+00:00", "payload": {}}))
    _log(storage, "logs/cocoa_2026-06-05T08-00-00Z.jsonl",
         _response("technical", "2026-06-05T08:00:05+00:00", 2.0))  # this month but older than 24h
    _log(storage, "logs/corn_2026-05-29T08-00-00Z.jsonl",
         _response("technical", "2026-05-29T08:00:05+00:00", 10.0))  # last month: excluded from both
    _log(storage, "portfolio/logs/cycle_2026-06-10T08-00-00Z.jsonl",
         _response("manager", "2026-06-10T08:05:00+00:00", 2.0))

    spend = llm_spend(storage, now=NOW)

    assert spend["24h"]["total"] == pytest.approx(5.6)
    assert spend["24h"]["by_agent"] == pytest.approx({"technical": 3.1, "coordinator": 0.5, "manager": 2.0})
    assert spend["24h"]["by_commodity"] == pytest.approx({"corn": 3.6, "portfolio": 2.0})
    assert spend["mtd"]["total"] == pytest.approx(7.6)
    assert spend["mtd"]["by_commodity"]["cocoa"] == pytest.approx(2.0)


def test_responses_without_usage_cost_nothing(tmp_path):
    storage = LocalStorage(tmp_path)
    _log(storage, "logs/corn_2026-06-10T08-00-00Z.jsonl",
         json.dumps({"agent": "news", "event_type": "response", "timestamp": "2026-06-10T08:00:00+00:00",
                     "payload": {"content": "x", "usage": None}}))

    assert llm_spend(storage, now=NOW)["24h"]["total"] == 0


def test_unpriced_responses_are_counted_not_guessed(tmp_path):
    storage = LocalStorage(tmp_path)
    _log(storage, "logs/corn_2026-06-10T08-00-00Z.jsonl",
         _response("news", "2026-06-10T08:00:05+00:00", None),
         _response("technical", "2026-06-10T08:00:06+00:00", 0.25),
         json.dumps({"agent": "news", "event_type": "response", "timestamp": "2026-06-10T08:00:07+00:00",
                     "payload": {"content": "x", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}}))

    day = llm_spend(storage, now=NOW)["24h"]

    assert day["total"] == pytest.approx(0.25)
    assert day["unpriced"] == 2
    assert day["by_agent"] == pytest.approx({"technical": 0.25})

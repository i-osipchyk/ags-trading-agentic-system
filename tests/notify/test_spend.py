import json
from datetime import datetime, timezone

import pytest

from ags.notify.spend import LlmPrices, llm_spend
from ags.storage import LocalStorage

NOW = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)
# $ per million tokens, round numbers so the expected values below are worked by hand.
PRICES = LlmPrices(cache_hit=0.1, cache_miss=1.0, output=2.0)


def _response(agent, timestamp, hit=0, miss=0, out=0):
    usage = {"prompt_tokens": hit + miss, "completion_tokens": out,
             "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": miss}
    return json.dumps({"seq": 0, "agent": agent, "event_type": "response", "timestamp": timestamp,
                       "payload": {"content": "x", "usage": usage}})


def _log(storage, key, *lines):
    storage.put(key, ("\n".join(lines) + "\n").encode())


def test_spend_is_summed_by_agent_and_commodity_for_24h_and_month_to_date(tmp_path):
    storage = LocalStorage(tmp_path)
    _log(storage, "logs/corn_2026-06-10T08-00-00Z.jsonl",
         _response("technical", "2026-06-10T08:00:05+00:00", hit=1_000_000, miss=1_000_000, out=1_000_000),  # 0.1+1+2
         _response("coordinator", "2026-06-10T08:01:00+00:00", miss=500_000),  # 0.5
         json.dumps({"seq": 2, "agent": "technical", "event_type": "output", "timestamp": "2026-06-10T08:02:00+00:00", "payload": {}}))
    _log(storage, "logs/cocoa_2026-06-05T08-00-00Z.jsonl",
         _response("technical", "2026-06-05T08:00:05+00:00", out=1_000_000))  # 2.0, this month but older than 24h
    _log(storage, "logs/corn_2026-05-29T08-00-00Z.jsonl",
         _response("technical", "2026-05-29T08:00:05+00:00", out=5_000_000))  # last month: excluded from both
    _log(storage, "portfolio/logs/cycle_2026-06-10T08-00-00Z.jsonl",
         _response("manager", "2026-06-10T08:05:00+00:00", miss=2_000_000))  # 2.0

    spend = llm_spend(storage, now=NOW, prices=PRICES)

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

    assert llm_spend(storage, now=NOW, prices=PRICES)["24h"]["total"] == 0

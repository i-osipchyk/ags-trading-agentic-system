import json
from datetime import datetime, timezone

from ags.notify.digest import compose_digest
from ags.storage import LocalStorage

NOW = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)  # 17:00 ET

CALLS = {
    "corn": {"call": "bullish", "conviction": 4}, "soybeans": {"call": "bearish", "conviction": 3},
    "wheat": {"call": "neutral", "conviction": 2}, "sugar": {"call": "bullish", "conviction": 1},
    "coffee": {"call": "bearish", "conviction": 5}, "cotton": {"call": "neutral", "conviction": 3}, "cocoa": None,
}

GOLDEN = """<b>AGS digest · 2026-06-10</b>

<b>Spend</b>
LLM 24h: $3.60 · MTD: $3.60
By agent (24h): technical $3.10, coordinator $0.50
By commodity (24h): corn $3.60
AWS (2026-06-09): $1.23

<b>Book</b> (cycle_2026-06-10T20-00-00Z)
Manager: equity $10,250.50 · free margin $9,000.00 · corn +2.0, soybeans -1.5
Shadow: equity $10,100.25 · free margin $9,500.00 · flat
Manager − shadow: +$150.25

<b>Latest calls</b>
corn: bullish (4)
soybeans: bearish (3)
wheat: neutral (2)
sugar: bullish (1)
coffee: bearish (5)
cotton: neutral (3)
cocoa: failed"""


def _cycle(storage, book, cycle_id, equity, free_margin, lots):
    storage.put(f"portfolio/{book}/cycles/{cycle_id}.json", json.dumps({
        "cycle_id": cycle_id, "equity": equity, "free_margin": free_margin, "closing_lots": lots,
        "calls": CALLS, "failed_commodities": ["cocoa"],
    }).encode())


def _response(agent, cost):
    return json.dumps({"agent": agent, "event_type": "response", "timestamp": "2026-06-10T08:00:00+00:00", "payload": {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": cost}}})


def _fixture(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.put("logs/corn_2026-06-10T08-00-00Z.jsonl", (
        _response("technical", 3.1) + "\n" + _response("coordinator", 0.5) + "\n"
    ).encode())
    zero = {c: 0.0 for c in CALLS}
    _cycle(storage, "manager", "cycle_2026-06-09T20-00-00Z", 10_000.0, 9_500.0, zero)
    _cycle(storage, "shadow", "cycle_2026-06-09T20-00-00Z", 10_000.0, 9_500.0, zero)
    _cycle(storage, "manager", "cycle_2026-06-10T20-00-00Z", 10_250.50, 9_000.0, {**zero, "corn": 2.0, "soybeans": -1.5})
    _cycle(storage, "shadow", "cycle_2026-06-10T20-00-00Z", 10_100.25, 9_500.0, zero)
    return storage


def test_digest_matches_the_golden_text(tmp_path):
    text = compose_digest(_fixture(tmp_path), now=NOW, aws_cost=lambda: ("2026-06-09", 1.23))

    assert text == GOLDEN


def test_digest_still_renders_when_cost_explorer_or_cycles_are_unavailable(tmp_path):
    def broken():
        raise RuntimeError("ce down")

    text = compose_digest(LocalStorage(tmp_path), now=NOW, aws_cost=broken)

    assert "AWS: unavailable" in text
    assert "No scheduled cycle yet" in text


def test_aws_cost_is_the_prior_utc_days_unblended_total():
    from ags.notify.aws_cost import prior_day_aws_cost

    class FakeCe:
        def __init__(self):
            self.request = None

        def get_cost_and_usage(self, **request):
            self.request = request
            return {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.2345", "Unit": "USD"}}}]}

    ce = FakeCe()

    assert prior_day_aws_cost(ce, now=NOW)() == ("2026-06-09", 1.2345)
    assert ce.request["TimePeriod"] == {"Start": "2026-06-09", "End": "2026-06-10"}
    assert ce.request["Granularity"] == "DAILY" and ce.request["Metrics"] == ["UnblendedCost"]

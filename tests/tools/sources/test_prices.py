import json
from datetime import date

from ags.tools.sources.prices import get_prices


class FakeCTraderClient:
    def __init__(self, bars_by_symbol):
        self._bars_by_symbol = bars_by_symbol
        self.calls = []

    def get_daily_bars(self, symbol_name, start, end):
        self.calls.append((symbol_name, start, end))
        return [bar for bar in self._bars_by_symbol[symbol_name] if start <= date.fromisoformat(bar["date"]) <= end]


CORN_BARS = [
    {"date": "2026-01-05", "open": 4.50, "high": 4.55, "low": 4.48, "close": 4.52, "volume": 1000},
    {"date": "2026-01-06", "open": 4.52, "high": 4.60, "low": 4.51, "close": 4.58, "volume": 1100},
    {"date": "2026-01-07", "open": 4.58, "high": 4.62, "low": 4.54, "close": 4.56, "volume": 900},
    {"date": "2026-01-09", "open": 4.56, "high": 4.61, "low": 4.53, "close": 4.60, "volume": 950},
]


def test_get_prices_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 5),
        end=date(2026, 1, 7),
        as_of=date(2026, 1, 7),
        client=client,
    )

    assert result == CORN_BARS[:3]
    assert len(client.calls) == 1
    stored = json.loads((tmp_path / "ctrader" / "daily_bars" / "corn" / "2026-01-06.json").read_text())
    assert stored == CORN_BARS[1]


def test_get_prices_second_call_for_same_range_is_a_noop(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})

    get_prices(tmp_path, commodity="corn", start=date(2026, 1, 5), end=date(2026, 1, 7), as_of=date(2026, 1, 7), client=client)
    result = get_prices(tmp_path, commodity="corn", start=date(2026, 1, 5), end=date(2026, 1, 7), as_of=date(2026, 1, 7), client=client)

    assert result == CORN_BARS[:3]
    assert len(client.calls) == 1


def test_get_prices_second_call_is_a_noop_when_range_includes_a_non_trading_day(tmp_path):
    # 2026-01-10 and 2026-01-11 are a Saturday/Sunday — the broker never
    # returns a bar for them, so the cache must remember "no bar here"
    # rather than treating the gap as still-missing on every call.
    client = FakeCTraderClient({"Corn": CORN_BARS})

    get_prices(tmp_path, commodity="corn", start=date(2026, 1, 9), end=date(2026, 1, 11), as_of=date(2026, 1, 11), client=client)
    get_prices(tmp_path, commodity="corn", start=date(2026, 1, 9), end=date(2026, 1, 11), as_of=date(2026, 1, 11), client=client)

    assert len(client.calls) == 1


def test_get_prices_never_requests_days_after_as_of(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 5),
        end=date(2026, 1, 7),
        as_of=date(2026, 1, 6),
        client=client,
    )

    assert result == CORN_BARS[:2]
    assert client.calls == [("Corn", date(2026, 1, 5), date(2026, 1, 6))]


def test_get_prices_closes_the_client_it_creates_for_itself(tmp_path, monkeypatch):
    # client=None is the production default: get_prices() constructs its own
    # CTraderPricesClient and must close it when done, rather than leaving a
    # live connection running in the background (it owns that lifecycle).
    closed = []

    class FakeOwnedClient:
        def __init__(self, config):
            pass

        def get_daily_bars(self, symbol_name, start, end):
            return [bar for bar in CORN_BARS if start <= date.fromisoformat(bar["date"]) <= end]

        def close(self):
            closed.append(True)

    class FakeConfig:
        @staticmethod
        def from_env():
            return "fake-config"

    monkeypatch.setattr("ags.tools.sources.prices.CTraderPricesClient", FakeOwnedClient)
    monkeypatch.setattr("ags.tools.sources.prices.Config", FakeConfig)

    get_prices(tmp_path, commodity="corn", start=date(2026, 1, 5), end=date(2026, 1, 7), as_of=date(2026, 1, 7))

    assert closed == [True]


def test_get_prices_does_not_close_a_client_the_caller_provided(tmp_path):
    # The caller owns the lifecycle of any client it injects — get_prices()
    # must not close it, since the caller may reuse it for further calls.
    closed = []

    class TrackedClient(FakeCTraderClient):
        def close(self):
            closed.append(True)

    client = TrackedClient({"Corn": CORN_BARS})

    get_prices(tmp_path, commodity="corn", start=date(2026, 1, 5), end=date(2026, 1, 7), as_of=date(2026, 1, 7), client=client)

    assert closed == []

import json
from datetime import date, timedelta

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


def _sessions(bars):
    # get_prices re-labels cTrader's open-time stamp to the session it covers.
    return [{**b, "date": (date.fromisoformat(b["date"]) + timedelta(days=1)).isoformat()} for b in bars]


def test_get_prices_fetches_through_client_and_writes_pit_store(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 6),
        end=date(2026, 1, 8),
        as_of=date(2026, 1, 9),
        client=client,
    )

    assert result == _sessions(CORN_BARS[:3])
    assert len(client.calls) == 1
    stored = json.loads((tmp_path / "ctrader" / "daily_bars" / "corn" / "2026-01-06.json").read_text())
    assert stored == CORN_BARS[1]


def test_get_prices_second_call_for_same_range_is_a_noop(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})
    kwargs = dict(commodity="corn", start=date(2026, 1, 6), end=date(2026, 1, 8), as_of=date(2026, 1, 9), client=client)

    get_prices(tmp_path, **kwargs)
    result = get_prices(tmp_path, **kwargs)

    assert result == _sessions(CORN_BARS[:3])
    assert len(client.calls) == 1


def test_get_prices_second_call_is_a_noop_when_range_includes_a_non_trading_day(tmp_path):
    # Fri/Sat stamps (2026-01-09/10) never carry a bar — a Friday session is
    # stamped Thursday. The cache must remember "no bar here" rather than
    # treating the gap as still-missing on every call.
    client = FakeCTraderClient({"Corn": CORN_BARS})
    kwargs = dict(commodity="corn", start=date(2026, 1, 9), end=date(2026, 1, 12), as_of=date(2026, 1, 13), client=client)

    get_prices(tmp_path, **kwargs)
    get_prices(tmp_path, **kwargs)

    assert len(client.calls) == 1


def test_get_prices_never_requests_sessions_that_have_not_closed_by_as_of(tmp_path):
    client = FakeCTraderClient({"Corn": CORN_BARS})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 6),
        end=date(2026, 1, 8),
        as_of=date(2026, 1, 7),
        client=client,
    )

    # Session 2026-01-07 is still in progress on 2026-01-07: only the 01-06 session (stamp 01-05) is public.
    assert result == _sessions(CORN_BARS[:1])
    assert client.calls == [("Corn", date(2026, 1, 5), date(2026, 1, 5))]


def test_get_prices_on_a_saturday_returns_fridays_close_as_the_latest_bar(tmp_path):
    # Friday 2026-01-09's session is stamped Thursday 2026-01-08; Fri/Sat stamps are empty.
    thursday_stamped = {"date": "2026-01-08", "open": 4.60, "high": 4.65, "low": 4.58, "close": 4.63, "volume": 800}
    client = FakeCTraderClient({"Corn": CORN_BARS + [thursday_stamped]})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 3),
        end=date(2026, 1, 10),
        as_of=date(2026, 1, 10),
        client=client,
    )

    assert result[-1]["date"] == "2026-01-09"
    assert result[-1]["close"] == 4.63


def test_get_prices_on_a_sunday_excludes_the_monday_session_stamped_that_day(tmp_path):
    sunday_stamped = {"date": "2026-01-11", "open": 4.70, "high": 4.75, "low": 4.68, "close": 4.72, "volume": 700}
    client = FakeCTraderClient({"Corn": CORN_BARS + [sunday_stamped]})

    result = get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 3),
        end=date(2026, 1, 11),
        as_of=date(2026, 1, 11),
        client=client,
    )

    assert "2026-01-12" not in {bar["date"] for bar in result}


def test_get_prices_does_not_cache_a_session_that_has_not_closed_yet(tmp_path):
    # Regression: caching "no bar yet" for an in-progress session stuck forever,
    # leaving a later weekend run with a week-old last close.
    client = FakeCTraderClient({"Corn": []})

    get_prices(
        tmp_path,
        commodity="corn",
        start=date(2026, 1, 5),
        end=date(2026, 1, 20),
        as_of=date(2026, 1, 20),
        client=client,
        today=date(2026, 1, 8),
    )

    cached = {p.stem for p in (tmp_path / "ctrader" / "daily_bars" / "corn").glob("*.json")}
    assert max(cached) == "2026-01-06"  # the 01-07 session (stamp 01-06) is the newest one closed by "today"


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

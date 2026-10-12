from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASpotEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOADynamicLeverage, ProtoOASymbol

from ags.tools.sources.prices import parse_quote, parse_symbol_specs


def _symbol():
    s = ProtoOASymbol(
        symbolId=42, digits=2, pipPosition=1, lotSize=5_000_000, minVolume=100_000,
        stepVolume=100_000, maxVolume=5_000_000_000, swapLong=-4.5, swapShort=1.25,
        swapCalculationType=0, swapRollover3Days=3, leverageId=7,
    )
    return s


def _leverage():
    lev = ProtoOADynamicLeverage(leverageId=7)
    lev.tiers.add(volume=0, leverage=2000)
    lev.tiers.add(volume=10_000_000, leverage=1000)
    return lev


def test_symbol_specs_are_flattened_to_plain_values_with_margin_rate_from_leverage():
    specs = parse_symbol_specs(_symbol(), _leverage())

    assert specs == {
        "symbol_id": 42,
        "lot_size": 5_000_000,
        "min_volume": 100_000,
        "step_volume": 100_000,
        "max_volume": 5_000_000_000,
        "swap_long": -4.5,
        "swap_short": 1.25,
        "swap_type": "PIPS",
        "triple_swap_day": "WEDNESDAY",
        "digits": 2,
        # 20:1 on the first tier (leverage is sent in cents of a ratio)
        "margin_rate": 0.05,
        "leverage_tiers": [{"volume": 0, "leverage": 20.0}, {"volume": 10_000_000, "leverage": 10.0}],
    }


def test_spot_event_becomes_bid_ask_with_exchange_timestamp():
    event = ProtoOASpotEvent(ctidTraderAccountId=1, symbolId=42, bid=451_250, ask=451_750, timestamp=1_767_600_000_000)

    assert parse_quote(event) == {
        "symbol_id": 42,
        "bid": 4.5125,
        "ask": 4.5175,
        "timestamp": "2026-01-05T08:00:00+00:00",
    }


# ---- fetch-through PIT cache -------------------------------------------------
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ags.tools.sources.prices import get_symbol_specs

ET = ZoneInfo("America/New_York")


class FakeMarketClient:
    def __init__(self, specs=None, quotes=None, h1=None):
        self.specs = specs or {}
        self.quotes = list(quotes or [])
        self.h1 = h1 or {}
        self.calls = []

    def get_symbol_specs(self, symbol_name):
        self.calls.append(("specs", symbol_name))
        return dict(self.specs[symbol_name])

    def get_quote(self, symbol_name):
        self.calls.append(("quote", symbol_name))
        return dict(self.quotes.pop(0))

    def get_h1_bars(self, symbol_name, start, end):
        self.calls.append(("h1", symbol_name, start, end))
        return [b for b in self.h1[symbol_name] if start <= datetime.fromisoformat(b["timestamp"]) < end]


CORN_SPECS = {"symbol_id": 42, "lot_size": 5_000_000, "swap_long": -4.5}


def test_specs_are_fetched_once_per_day_and_reused_by_later_cycles_that_day(tmp_path):
    client = FakeMarketClient(specs={"Corn": CORN_SPECS})
    morning = datetime(2026, 1, 6, 4, 0, tzinfo=ET)
    afternoon = datetime(2026, 1, 6, 16, 0, tzinfo=ET)

    first = get_symbol_specs(tmp_path, commodity="corn", as_of=morning, client=client, now=morning)
    second = get_symbol_specs(tmp_path, commodity="corn", as_of=afternoon, client=client, now=afternoon)

    assert first["lot_size"] == second["lot_size"] == 5_000_000
    assert len(client.calls) == 1


def test_specs_are_refetched_the_next_day_so_swap_changes_are_picked_up(tmp_path):
    client = FakeMarketClient(specs={"Corn": CORN_SPECS})
    day1 = datetime(2026, 1, 6, 16, 0, tzinfo=ET)
    day2 = datetime(2026, 1, 7, 4, 0, tzinfo=ET)
    get_symbol_specs(tmp_path, commodity="corn", as_of=day1, client=client, now=day1)
    client.specs["Corn"] = {**CORN_SPECS, "swap_long": -9.0}

    latest = get_symbol_specs(tmp_path, commodity="corn", as_of=day2, client=client, now=day2)

    assert latest["swap_long"] == -9.0
    assert len(client.calls) == 2


def test_specs_as_of_the_past_return_what_was_known_then_and_never_fetch(tmp_path):
    client = FakeMarketClient(specs={"Corn": CORN_SPECS})
    day1 = datetime(2026, 1, 6, 16, 0, tzinfo=ET)
    get_symbol_specs(tmp_path, commodity="corn", as_of=day1, client=client, now=day1)
    client.specs["Corn"] = {**CORN_SPECS, "swap_long": -9.0}
    later = datetime(2026, 1, 20, 16, 0, tzinfo=ET)

    replay = get_symbol_specs(tmp_path, commodity="corn", as_of=day1, client=client, now=later)
    before_any = get_symbol_specs(tmp_path, commodity="corn", as_of=datetime(2026, 1, 1, tzinfo=ET), client=client, now=later)

    assert replay["swap_long"] == -4.5
    assert before_any is None
    assert len(client.calls) == 1


from ags.tools.sources.prices import get_quote

QUOTE = {"symbol_id": 42, "bid": 4.5125, "ask": 4.5175, "timestamp": "2026-01-06T21:00:00+00:00"}


def test_live_quote_is_captured_and_replaying_that_instant_returns_it_without_fetching(tmp_path):
    client = FakeMarketClient(quotes=[QUOTE])
    capture = datetime(2026, 1, 6, 16, 0, 3, tzinfo=ET)

    live = get_quote(tmp_path, commodity="corn", as_of=capture, client=client, now=capture)
    replay = get_quote(tmp_path, commodity="corn", as_of=capture, client=client, now=capture + timedelta(days=3))

    assert live == replay == QUOTE
    assert len(client.calls) == 1


def test_quote_is_not_visible_before_it_was_captured(tmp_path):
    client = FakeMarketClient(quotes=[QUOTE])
    capture = datetime(2026, 1, 6, 16, 0, 3, tzinfo=ET)
    get_quote(tmp_path, commodity="corn", as_of=capture, client=client, now=capture)

    earlier = get_quote(
        tmp_path, commodity="corn", as_of=capture - timedelta(seconds=1), client=client, now=capture + timedelta(days=1)
    )

    assert earlier is None


def test_each_live_cycle_takes_a_fresh_quote(tmp_path):
    second = {**QUOTE, "bid": 4.60, "ask": 4.61}
    client = FakeMarketClient(quotes=[QUOTE, second])
    t1 = datetime(2026, 1, 6, 16, 0, tzinfo=ET)
    t2 = datetime(2026, 1, 7, 4, 0, tzinfo=ET)

    get_quote(tmp_path, commodity="corn", as_of=t1, client=client, now=t1)
    latest = get_quote(tmp_path, commodity="corn", as_of=t2, client=client, now=t2)

    assert latest["bid"] == 4.60


from ags.tools.sources.prices import get_h1_bars


def _h1(hour_utc, day=6):
    ts = datetime(2026, 1, day, hour_utc, tzinfo=timezone.utc).isoformat()
    return {"timestamp": ts, "open": 4.5, "high": 4.6, "low": 4.4, "close": 4.55, "volume": 10}


H1 = {"Corn": [_h1(h) for h in range(14, 22)]}  # 14:00..21:00 UTC bars on Jan 6


def test_h1_bars_exclude_the_hour_still_in_progress_at_as_of(tmp_path):
    client = FakeMarketClient(h1=H1)
    now = datetime(2026, 1, 6, 18, 30, tzinfo=timezone.utc)  # 18:00 bar is still forming

    bars = get_h1_bars(
        tmp_path, commodity="corn", start=datetime(2026, 1, 6, 14, tzinfo=timezone.utc),
        end=datetime(2026, 1, 6, 22, tzinfo=timezone.utc), as_of=now, client=client, now=now,
    )

    assert [b["timestamp"][11:13] for b in bars] == ["14", "15", "16", "17"]


def test_h1_bars_already_captured_are_not_refetched(tmp_path):
    client = FakeMarketClient(h1=H1)
    now = datetime(2026, 1, 6, 18, 30, tzinfo=timezone.utc)
    kwargs = dict(
        commodity="corn", start=datetime(2026, 1, 6, 14, tzinfo=timezone.utc),
        end=datetime(2026, 1, 6, 22, tzinfo=timezone.utc), as_of=now, client=client, now=now,
    )
    first = get_h1_bars(tmp_path, **kwargs)

    second = get_h1_bars(tmp_path, **kwargs)

    assert first == second
    assert len(client.calls) == 1


def test_later_cycle_fetches_only_the_new_hours_and_returns_the_full_range(tmp_path):
    client = FakeMarketClient(h1=H1)
    start = datetime(2026, 1, 6, 14, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 22, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 6, 16, 30, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 6, 19, 30, tzinfo=timezone.utc)
    get_h1_bars(tmp_path, commodity="corn", start=start, end=end, as_of=t1, client=client, now=t1)

    bars = get_h1_bars(tmp_path, commodity="corn", start=start, end=end, as_of=t2, client=client, now=t2)

    assert [b["timestamp"][11:13] for b in bars] == ["14", "15", "16", "17", "18"]
    assert client.calls[1][2:] == (datetime(2026, 1, 6, 16, tzinfo=timezone.utc), datetime(2026, 1, 6, 19, tzinfo=timezone.utc))


def test_replaying_an_earlier_as_of_does_not_see_bars_captured_later(tmp_path):
    client = FakeMarketClient(h1=H1)
    start = datetime(2026, 1, 6, 14, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 22, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 6, 16, 30, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 6, 19, 30, tzinfo=timezone.utc)
    get_h1_bars(tmp_path, commodity="corn", start=start, end=end, as_of=t1, client=client, now=t1)
    get_h1_bars(tmp_path, commodity="corn", start=start, end=end, as_of=t2, client=client, now=t2)

    replay = get_h1_bars(tmp_path, commodity="corn", start=start, end=end, as_of=t1, client=client, now=t2)

    assert [b["timestamp"][11:13] for b in replay] == ["14", "15"]
    assert len(client.calls) == 2


def test_h1_trendbar_is_decoded_with_its_open_instant():
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbar

    from ags.tools.sources.prices import parse_h1_bar

    bar = ProtoOATrendbar(
        low=451_000, deltaOpen=100, deltaHigh=500, deltaClose=250, volume=77,
        utcTimestampInMinutes=29_460_000,  # 1_767_600_000 s = 2026-01-05T08:00Z
    )

    assert parse_h1_bar(bar) == {
        "timestamp": "2026-01-05T08:00:00+00:00",
        "open": 4.511,
        "high": 4.515,
        "low": 4.51,
        "close": 4.5125,
        "volume": 77,
    }

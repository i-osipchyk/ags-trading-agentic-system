from datetime import date, datetime, time, timedelta

import pytest

from ags.tools.pit_store import ET
from ags.config import Config
from ags.tools.sources import prices
from ags.tools.sources.prices import CTraderPricesClient, get_prices


def _last_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.mark.integration
def test_get_prices_pulls_corn_from_real_ctrader_and_second_call_is_a_noop(tmp_path):
    config = Config.from_env()
    client = CTraderPricesClient(config)

    calls = []
    real_get_daily_bars = client.get_daily_bars

    def counted_get_daily_bars(symbol_name, start, end):
        calls.append((symbol_name, start, end))
        return real_get_daily_bars(symbol_name, start, end)

    client.get_daily_bars = counted_get_daily_bars

    end = _last_weekday(date.today() - timedelta(days=1))
    start = _last_weekday(end - timedelta(days=4))

    first = get_prices(tmp_path, commodity="corn", start=start, end=end, as_of=datetime.combine(end + timedelta(days=1), time(4, 0), tzinfo=ET), client=client)

    assert first, "expected at least one daily bar from the real cTrader feed"
    stored_files = list((tmp_path / prices.SOURCE / prices.REPORT / "corn").glob("*.json"))
    assert stored_files, "fetch-through-cache should have written at least one PIT file"
    assert len(calls) == 1

    second = get_prices(tmp_path, commodity="corn", start=start, end=end, as_of=datetime.combine(end + timedelta(days=1), time(4, 0), tzinfo=ET), client=client)

    assert second == first
    assert len(calls) == 1, "second call for the same range must be a cache no-op, not a second fetch"


@pytest.mark.integration
def test_market_data_for_the_book_pulls_specs_quote_and_h1_bars_for_one_symbol(tmp_path):
    from datetime import datetime, timezone

    from ags.tools.sources.prices import get_h1_bars, get_quote, get_symbol_specs

    client = CTraderPricesClient(Config.from_env())
    try:
        now = datetime.now(timezone.utc)
        specs = get_symbol_specs(tmp_path, commodity="corn", as_of=now, client=client, now=now)
        quote = get_quote(tmp_path, commodity="corn", as_of=now, client=client, now=now)
        bars = get_h1_bars(
            tmp_path, commodity="corn", start=now - timedelta(days=3), end=now, as_of=now, client=client, now=now
        )
    finally:
        client.close()

    assert specs["lot_size"] > 0 and specs["min_volume"] > 0 and specs["margin_rate"] > 0
    assert 0 < quote["bid"] <= quote["ask"]
    assert bars, "expected H1 bars in the last 3 days"
    assert list((tmp_path / prices.SOURCE).rglob("*.json"))

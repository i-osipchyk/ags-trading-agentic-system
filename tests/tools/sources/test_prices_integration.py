from datetime import date, timedelta

import pytest

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

    first = get_prices(tmp_path, commodity="corn", start=start, end=end, as_of=end, client=client)

    assert first, "expected at least one daily bar from the real cTrader feed"
    stored_files = list((tmp_path / prices.SOURCE / prices.REPORT / "corn").glob("*.json"))
    assert stored_files, "fetch-through-cache should have written at least one PIT file"
    assert len(calls) == 1

    second = get_prices(tmp_path, commodity="corn", start=start, end=end, as_of=end, client=client)

    assert second == first
    assert len(calls) == 1, "second call for the same range must be a cache no-op, not a second fetch"

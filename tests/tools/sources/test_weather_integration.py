from datetime import date

import pytest

from ags.tools.sources import weather
from ags.tools.sources.weather import USWeatherClient, get_weather


@pytest.mark.integration
def test_get_weather_pulls_iowa_conditions_from_real_sources_and_second_call_is_a_noop(tmp_path):
    client = USWeatherClient()

    calls = []
    real_get_drought = client.get_drought_conditions

    def counted_get_drought(region, *, as_of):
        calls.append((region, as_of))
        return real_get_drought(region, as_of=as_of)

    client.get_drought_conditions = counted_get_drought

    as_of = date.today()

    first = get_weather(tmp_path, region="IA", as_of=as_of, client=client)

    assert first["drought"] is not None
    assert first["outlook"] is not None
    stored_drought_files = list((tmp_path / weather.SOURCE_DROUGHT / weather.REPORT_DROUGHT / "IA").glob("*.json"))
    assert stored_drought_files
    assert len(calls) == 1

    second = get_weather(tmp_path, region="IA", as_of=as_of, client=client)

    assert second == first
    assert len(calls) == 1, "second call for the same region/as_of must be a cache no-op"

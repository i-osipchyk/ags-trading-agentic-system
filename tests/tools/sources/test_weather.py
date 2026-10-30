import json
from datetime import date, datetime

import pytest

from ags.tools.sources import weather
from ags.tools.sources.weather import get_weather
from ags.tools.pit_store import ET


class FakeWeatherClient:
    def __init__(self, drought_by_region=None, outlook_by_region=None):
        self._drought_by_region = drought_by_region or {}
        self._outlook_by_region = outlook_by_region or {}
        self.drought_calls = []
        self.outlook_calls = []

    def get_drought_conditions(self, region, *, as_of):
        self.drought_calls.append((region, as_of))
        return self._drought_by_region.get(region)

    def get_outlook(self, region, *, as_of):
        self.outlook_calls.append((region, as_of))
        return self._outlook_by_region.get(region)


IOWA_DROUGHT = {
    "map_date": "2026-09-29",
    "none_sq_mi": 51520.52,
    "d0_sq_mi": 4790.98,
    "d1_sq_mi": 21.07,
    "d2_sq_mi": 0.0,
    "d3_sq_mi": 0.0,
    "d4_sq_mi": 0.0,
}

IOWA_OUTLOOK = {
    "issued": "2026-09-30",
    "valid_period": "Oct 2026",
    "temp": {"category": "Above", "probability": 40.0},
    "precip": {"category": "EC", "probability": 33.0},
}


def test_get_weather_fetches_drought_conditions_through_client_and_writes_pit_store(tmp_path):
    client = FakeWeatherClient(drought_by_region={"IA": IOWA_DROUGHT}, outlook_by_region={"IA": IOWA_OUTLOOK})

    result = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["drought"] == IOWA_DROUGHT
    assert len(client.drought_calls) == 1
    assert client.drought_calls[0] == ("IA", datetime(2026, 10, 1, 16, 0, tzinfo=ET))

    stored_files = list((tmp_path / "usdm" / "drought_severity" / "IA").glob("*.json"))
    assert len(stored_files) == 1
    assert json.loads(stored_files[0].read_text()) == IOWA_DROUGHT


def test_get_weather_second_call_for_the_same_week_is_a_noop(tmp_path):
    client = FakeWeatherClient(drought_by_region={"IA": IOWA_DROUGHT}, outlook_by_region={"IA": IOWA_OUTLOOK})

    first = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)
    second = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 2, 16, 0, tzinfo=ET), client=client)

    assert second["drought"] == first["drought"]
    assert len(client.drought_calls) == 1


def test_get_weather_drops_a_drought_record_dated_after_as_of(tmp_path):
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee — a leaking client must not be able to leak through here.
    future_dated = {**IOWA_DROUGHT, "map_date": "2026-10-10"}
    client = FakeWeatherClient(drought_by_region={"IA": future_dated})

    result = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["drought"] is None


def test_get_weather_fetches_outlook_through_client_and_writes_pit_store(tmp_path):
    client = FakeWeatherClient(drought_by_region={"IA": IOWA_DROUGHT}, outlook_by_region={"IA": IOWA_OUTLOOK})

    result = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["outlook"] == IOWA_OUTLOOK
    assert len(client.outlook_calls) == 1
    assert client.outlook_calls[0] == ("IA", datetime(2026, 10, 1, 16, 0, tzinfo=ET))

    stored_files = list((tmp_path / "noaa_cpc" / "monthly_outlook" / "IA").glob("*.json"))
    assert len(stored_files) == 1
    assert json.loads(stored_files[0].read_text()) == IOWA_OUTLOOK


def test_get_weather_drops_an_outlook_record_dated_after_as_of(tmp_path):
    future_dated = {**IOWA_OUTLOOK, "issued": "2026-10-10"}
    client = FakeWeatherClient(outlook_by_region={"IA": future_dated})

    result = get_weather(tmp_path, region="IA", as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET), client=client)

    assert result["outlook"] is None


class _OpenMeteoResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _open_meteo_payload() -> dict:
    # 14 days before 2026-10-30 followed by 7 days from it (forecast_days includes today).
    past_precip = [0, 0, 0, 10, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0]
    past_tmin = [12.0] * 14
    past_tmin[5] = 9.5
    past_tmax = [30.0] * 14
    past_tmax[2] = 33.0
    return {
        "daily": {
            "time": [f"2026-10-{day}" for day in range(16, 32)] + [f"2026-11-0{day}" for day in range(1, 6)],
            "precipitation_sum": past_precip + [0, 20, 0, 0, 0, 0, 3],
            "temperature_2m_min": past_tmin + [11.0, 10.0, 8.0, 12.0, 12.0, 12.0, 12.0],
            "temperature_2m_max": past_tmax + [28.0, 29.0, 31.5, 27.0, 27.0, 27.0, 27.0],
        }
    }


def test_get_weather_summarises_open_meteo_history_and_forecast_for_a_non_us_region(tmp_path, monkeypatch):
    requests_made = []

    def fake_get(url, **kwargs):
        requests_made.append((url, kwargs.get("params", {})))
        return _OpenMeteoResponse(_open_meteo_payload())

    monkeypatch.setattr(weather.requests, "get", fake_get)

    result = get_weather(tmp_path, region="BR-MG", as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET))

    assert result["forecast"] == {
        "issued": "2026-10-30",
        "past_14d": {"precip_mm": 15.0, "tmin_c": 9.5, "tmax_c": 33.0},
        "next_7d": {"precip_mm": 23.0, "tmin_c": 8.0, "tmax_c": 31.5},
    }
    assert len(requests_made) == 1
    assert requests_made[0][1]["latitude"] == -21.2  # Minas Gerais, Brazil's main arabica state


def test_get_weather_refetches_open_meteo_every_cycle_and_archives_each_pull(tmp_path, monkeypatch):
    requests_made = []

    def fake_get(url, **kwargs):
        requests_made.append(url)
        return _OpenMeteoResponse(_open_meteo_payload())

    monkeypatch.setattr(weather.requests, "get", fake_get)

    morning = get_weather(tmp_path, region="BR-MG", as_of=datetime(2026, 10, 30, 6, 0, tzinfo=ET))
    midday = get_weather(tmp_path, region="BR-MG", as_of=datetime(2026, 10, 30, 12, 30, tzinfo=ET))

    assert len(requests_made) == 2  # a forecast cached at 06:00 would be stale at 12:30
    archived = sorted((tmp_path / "open_meteo" / "forecast" / "BR-MG").glob("*.json"))
    assert [json.loads(path.read_text()) for path in archived] == [morning["forecast"], midday["forecast"]]


@pytest.mark.parametrize("commodity", ["coffee", "cocoa", "sugar", "cotton"])
def test_every_region_a_commodity_is_offered_can_be_fetched(tmp_path, monkeypatch, commodity):
    monkeypatch.setattr(weather.requests, "get", lambda url, **kwargs: _OpenMeteoResponse(_open_meteo_payload()))
    us_client = FakeWeatherClient(drought_by_region={}, outlook_by_region={})

    for region in weather.regions_for(commodity):
        client = us_client if region in weather.SUPPORTED_REGIONS else None
        result = get_weather(tmp_path, region=region, as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET), client=client)
        assert result["region"] == region

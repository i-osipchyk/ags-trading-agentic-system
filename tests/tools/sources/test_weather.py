import json
from datetime import date

from ags.tools.sources.weather import get_weather


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

    result = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 1), client=client)

    assert result["drought"] == IOWA_DROUGHT
    assert len(client.drought_calls) == 1
    assert client.drought_calls[0] == ("IA", date(2026, 10, 1))

    stored_files = list((tmp_path / "usdm" / "drought_severity" / "IA").glob("*.json"))
    assert len(stored_files) == 1
    assert json.loads(stored_files[0].read_text()) == IOWA_DROUGHT


def test_get_weather_second_call_for_the_same_week_is_a_noop(tmp_path):
    client = FakeWeatherClient(drought_by_region={"IA": IOWA_DROUGHT}, outlook_by_region={"IA": IOWA_OUTLOOK})

    first = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 1), client=client)
    second = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 2), client=client)

    assert second["drought"] == first["drought"]
    assert len(client.drought_calls) == 1


def test_get_weather_drops_a_drought_record_dated_after_as_of(tmp_path):
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee — a leaking client must not be able to leak through here.
    future_dated = {**IOWA_DROUGHT, "map_date": "2026-10-10"}
    client = FakeWeatherClient(drought_by_region={"IA": future_dated})

    result = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 1), client=client)

    assert result["drought"] is None


def test_get_weather_fetches_outlook_through_client_and_writes_pit_store(tmp_path):
    client = FakeWeatherClient(drought_by_region={"IA": IOWA_DROUGHT}, outlook_by_region={"IA": IOWA_OUTLOOK})

    result = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 1), client=client)

    assert result["outlook"] == IOWA_OUTLOOK
    assert len(client.outlook_calls) == 1
    assert client.outlook_calls[0] == ("IA", date(2026, 10, 1))

    stored_files = list((tmp_path / "noaa_cpc" / "monthly_outlook" / "IA").glob("*.json"))
    assert len(stored_files) == 1
    assert json.loads(stored_files[0].read_text()) == IOWA_OUTLOOK


def test_get_weather_drops_an_outlook_record_dated_after_as_of(tmp_path):
    future_dated = {**IOWA_OUTLOOK, "issued": "2026-10-10"}
    client = FakeWeatherClient(outlook_by_region={"IA": future_dated})

    result = get_weather(tmp_path, region="IA", as_of=date(2026, 10, 1), client=client)

    assert result["outlook"] is None

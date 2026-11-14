from datetime import datetime

from ags.freshness import source_freshness
from ags.tools.pit_store import ET, write_pit

AS_OF = datetime(2026, 11, 13, 6, 0, tzinfo=ET)


def _by_source(entries):
    return {e["source"]: e for e in entries}


def _store_weather(tmp_path, region, map_date, issued):
    from datetime import date
    write_pit(tmp_path, source="usdm", report="drought_severity", symbol=region,
              release_date=date.fromisoformat(map_date), content={"map_date": map_date})
    write_pit(tmp_path, source="noaa_cpc", report="monthly_outlook", symbol=region,
              release_date=date.fromisoformat(issued), content={"issued": issued})


def test_a_drought_map_older_than_ten_days_is_stale_and_fresh_ones_are_not(tmp_path):
    _store_weather(tmp_path, "IA", "2026-10-06", "2026-10-15")
    outputs = {"supply_demand": {"latest_report": {"name": "WASDE", "date": "2026-11-10"}}, "news": {"headlines": []}}

    sources = _by_source(source_freshness(tmp_path, commodity="corn", as_of=AS_OF, outputs=outputs))

    assert sources["drought_map"]["age_days"] == 38 and sources["drought_map"]["stale"]
    assert not sources["cpc_outlook"]["stale"]
    assert sources["supply_demand"]["age_days"] == 3 and not sources["supply_demand"]["stale"]


def test_wasde_older_than_forty_days_is_stale(tmp_path):
    outputs = {"supply_demand": {"latest_report": {"name": "WASDE", "date": "2026-09-30"}}, "news": {"headlines": []}}

    sources = _by_source(source_freshness(tmp_path, commodity="corn", as_of=AS_OF, outputs=outputs))

    assert sources["supply_demand"]["stale"] and sources["supply_demand"]["detail"] == "WASDE"


def test_news_is_judged_on_its_newest_headline_and_no_headlines_is_stale(tmp_path):
    fresh = {"news": {"headlines": [{"date": "2026-11-10"}, {"date": "2026-10-01"}], "carried_forward": True}}
    empty = {"news": {"headlines": []}}

    fresh_entry = _by_source(source_freshness(tmp_path, commodity="corn", as_of=AS_OF, outputs=fresh))["news"]
    empty_entry = _by_source(source_freshness(tmp_path, commodity="corn", as_of=AS_OF, outputs=empty))["news"]

    assert fresh_entry["latest"] == "2026-11-10" and not fresh_entry["stale"]
    assert fresh_entry["detail"] == "carried forward"
    assert empty_entry["stale"] and empty_entry["latest"] is None


def test_a_commodity_without_us_regions_has_no_drought_or_outlook_entry(tmp_path):
    sources = _by_source(source_freshness(tmp_path, commodity="coffee", as_of=AS_OF, outputs={}))

    assert set(sources) == {"supply_demand", "news"}


def test_cocoa_grinds_use_the_quarterly_limit(tmp_path):
    outputs = {"supply_demand": {"latest_report": {"name": "grinds", "date": "2026-08-01"}}}

    sources = _by_source(source_freshness(tmp_path, commodity="cocoa", as_of=AS_OF, outputs=outputs))

    assert "cocoa_grinds" in sources and not sources["cocoa_grinds"]["stale"]

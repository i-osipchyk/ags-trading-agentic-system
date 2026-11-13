from datetime import date, datetime

from ags.tools.pit_store import ET, is_released
from ags.tools.supply_demand import compute_supply_demand


class FakeUsdaClient:
    """Serves the latest release at or before as_of from a canned archive, like the ESMIS client."""

    def __init__(self, releases_by_symbol=None):
        self._releases = releases_by_symbol or {}

    def get_report(self, report, symbol, *, as_of):
        eligible = [
            r for r in self._releases.get(symbol, [])
            if is_released("usda_esmis", date.fromisoformat(r["release_date"]), as_of)
        ]
        return max(eligible, key=lambda r: r["release_date"]) if eligible else None


CORN_AUG = {"release_date": "2026-08-12", "marketing_year": "2026/27 Proj.", "ending_stocks": 1900.0, "stocks_to_use": 12.0}
CORN_SEP = {"release_date": "2026-09-11", "marketing_year": "2026/27 Proj.", "ending_stocks": 2100.0, "stocks_to_use": 13.1}
_OCT_1 = datetime(2026, 10, 1, 16, 0, tzinfo=ET)


def _compute(tmp_path, commodity="corn", releases=None, as_of=_OCT_1, **kwargs):
    return compute_supply_demand(
        tmp_path, commodity=commodity, as_of=as_of, usda_client=FakeUsdaClient({commodity: releases or []}), **kwargs
    )


def test_a_release_with_an_earlier_one_in_the_same_year_gives_the_revision_and_the_trend(tmp_path):
    result = _compute(tmp_path, releases=[CORN_AUG, CORN_SEP])

    assert result == {
        "latest_report": {"name": "WASDE", "date": "2026-09-11"},
        "key_figures": [
            {"figure": "ending_stocks (million bushels)", "value": 2100.0, "prior": 1900.0, "revision_direction": "up"},
            {"figure": "stocks_to_use (%)", "value": 13.1, "prior": 12.0, "revision_direction": "up"},
        ],
        "stocks_to_use": {"value": 13.1, "trend": "loosening"},
        "demand_indicators": None,
        "degraded": False,
    }


def test_a_release_that_left_stocks_to_use_unchanged_reports_unchanged_not_a_trend(tmp_path):
    flat = {**CORN_SEP, "release_date": "2026-09-25"}

    result = _compute(tmp_path, releases=[CORN_SEP, flat], as_of=datetime(2026, 10, 1, 16, 0, tzinfo=ET))

    assert result["stocks_to_use"] == {"value": 13.1, "trend": "unchanged"}
    assert {f["revision_direction"] for f in result["key_figures"]} == {"unchanged"}


def test_a_release_with_nothing_earlier_to_compare_with_has_no_revision_and_no_trend(tmp_path):
    result = _compute(tmp_path, releases=[CORN_SEP])

    assert result["key_figures"] == [
        {"figure": "ending_stocks (million bushels)", "value": 2100.0, "prior": None, "revision_direction": None},
        {"figure": "stocks_to_use (%)", "value": 13.1, "prior": None, "revision_direction": None},
    ]
    assert result["stocks_to_use"] == {"value": 13.1, "trend": None}
    assert result["degraded"] is False  # a first release is not a gap


def test_an_earlier_release_for_another_marketing_year_is_not_a_revision(tmp_path):
    # The Dec 2025 release is the 2025/26 balance sheet; comparing 2026/27 with it showed a false "tightening".
    last_year = {**CORN_AUG, "release_date": "2025-12-09", "marketing_year": "2025/26 Proj.", "ending_stocks": 2029.0}

    result = _compute(tmp_path, releases=[last_year, CORN_SEP])

    assert result["key_figures"][0]["prior"] is None
    assert result["stocks_to_use"]["trend"] is None


# The Nov 2026 WASDE for corn: every row carries last month's forecast of the same marketing year.
CORN_NOV = {
    "release_date": "2026-11-10", "marketing_year": "2026/27 Proj.", "ending_stocks": 1758.0, "stocks_to_use": 10.82,
    "prior_stocks_to_use": 11.34, "units": "million bushels; yield bu/acre; area million acres; price $/bu",
    "figures": {
        "beginning_stocks": {"value": 2095.0, "prior": 2095.0},
        "yield": {"value": 179.6, "prior": 181.2},
        "production": {"value": 15893.0, "prior": 16034.0},
        "exports": {"value": 3275.0, "prior": 3300.0},
        "ethanol_use": {"value": 5650.0, "prior": 5650.0},
        "total_use": {"value": 16255.0, "prior": 16305.0},
        "ending_stocks": {"value": 1758.0, "prior": 1849.0},
        "avg_farm_price": {"value": 4.6, "prior": 4.7},
    },
}
_NOV_11 = datetime(2026, 11, 11, 6, 0, tzinfo=ET)


def test_every_revised_figure_is_reported_largest_relative_change_first_after_the_two_headline_ones(tmp_path):
    result = _compute(tmp_path, releases=[CORN_NOV], as_of=_NOV_11)

    assert result["key_figures"] == [
        {"figure": "ending_stocks (million bushels)", "value": 1758.0, "prior": 1849.0, "revision_direction": "down"},
        {"figure": "stocks_to_use (%)", "value": 10.82, "prior": 11.34, "revision_direction": "down"},
        {"figure": "avg_farm_price ($/bu)", "value": 4.6, "prior": 4.7, "revision_direction": "down"},
        {"figure": "yield (bu/acre)", "value": 179.6, "prior": 181.2, "revision_direction": "down"},
        {"figure": "production (million bushels)", "value": 15893.0, "prior": 16034.0, "revision_direction": "down"},
        {"figure": "exports (million bushels)", "value": 3275.0, "prior": 3300.0, "revision_direction": "down"},
        {"figure": "total_use (million bushels)", "value": 16255.0, "prior": 16305.0, "revision_direction": "down"},
    ]  # beginning stocks and ethanol use did not move, so they are left out
    assert result["stocks_to_use"] == {"value": 10.82, "trend": "tightening"}


_NO_SIGNAL = {
    "latest_report": None, "key_figures": [], "stocks_to_use": None, "demand_indicators": None, "degraded": True,
}


def test_with_no_release_public_yet_there_is_no_signal_and_the_output_is_degraded(tmp_path):
    assert _compute(tmp_path, releases=[CORN_SEP], as_of=datetime(2026, 9, 1, 16, 0, tzinfo=ET)) == _NO_SIGNAL


class FailingUsdaClient:
    def get_report(self, report, symbol, *, as_of):
        raise ConnectionError("ESMIS request failed")


def test_a_source_failure_degrades_the_output_instead_of_raising(tmp_path):
    result = compute_supply_demand(tmp_path, commodity="corn", as_of=_OCT_1, usda_client=FailingUsdaClient())

    assert result == _NO_SIGNAL


def test_cotton_figures_carry_cottons_own_units(tmp_path):
    nov = {
        "release_date": "2026-11-10", "marketing_year": "2026/27 Proj.", "ending_stocks": 3.7, "stocks_to_use": 27.01,
        "prior_stocks_to_use": 27.54,
        "figures": {
            "ending_stocks": {"value": 3.7, "prior": 3.8}, "total_use": {"value": 13.7, "prior": 13.8},
            "yield": {"value": 775.0, "prior": 790.0}, "avg_farm_price": {"value": 76.0, "prior": 77.0},
        },
    }

    result = _compute(tmp_path, "cotton", releases=[nov], as_of=_NOV_11)

    assert [f["figure"] for f in result["key_figures"]] == [
        "ending_stocks (million 480-lb bales)", "stocks_to_use (%)", "yield (lb/acre)",
        "avg_farm_price (cents/lb)", "total_use (million 480-lb bales)",
    ]


def test_sugar_figures_are_in_thousand_short_tons(tmp_path):
    nov = {
        "release_date": "2026-11-10", "marketing_year": "2026/27 Proj.", "ending_stocks": 1723.0, "stocks_to_use": 13.87,
        "prior_stocks_to_use": 14.89,
        "figures": {
            "ending_stocks": {"value": 1723.0, "prior": 1865.0}, "total_use": {"value": 12426.0, "prior": 12522.0},
            "production": {"value": 8520.0, "prior": 8768.0},
        },
    }

    result = _compute(tmp_path, "sugar", releases=[nov], as_of=_NOV_11)

    assert [f["figure"] for f in result["key_figures"]] == [
        "ending_stocks (1,000 short tons)", "stocks_to_use (%)", "production (1,000 short tons)",
        "total_use (1,000 short tons)",
    ]
    assert result["stocks_to_use"]["trend"] == "tightening"


def test_coffee_comes_from_the_fas_report_with_world_stocks_in_thousand_bags(tmp_path):
    jun = {"release_date": "2026-06-25", "marketing_year": "2026/27", "ending_stocks": 24000.0, "stocks_to_use": 14.0}
    jul = {"release_date": "2026-07-22", "marketing_year": "2026/27", "ending_stocks": 26283.0, "stocks_to_use": 14.62}

    result = _compute(tmp_path, "coffee", releases=[jun, jul], as_of=datetime(2026, 8, 1, 16, 0, tzinfo=ET))

    assert result["latest_report"] == {"name": "Coffee: World Markets and Trade", "date": "2026-07-22"}
    assert result["key_figures"] == [
        {"figure": "ending_stocks (1,000 60-kg bags, world)", "value": 26283.0, "prior": 24000.0, "revision_direction": "up"},
        {"figure": "stocks_to_use (%)", "value": 14.62, "prior": 14.0, "revision_direction": "up"},
    ]
    assert result["stocks_to_use"] == {"value": 14.62, "trend": "loosening"}


GRINDS = {
    "europe": {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": 348215, "yoy_pct": 3.22},
    "asia": {"quarter": "2026-Q3", "published": "2026-10-15", "tonnes": None, "yoy_pct": 29.22},
}


class FakeGrindsClient:
    def __init__(self, by_region):
        self._by_region = by_region

    def get_release(self, region, *, as_of):
        return self._by_region.get(region)


def _cocoa(tmp_path, by_region):
    return compute_supply_demand(
        tmp_path, commodity="cocoa", as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET), grinds_client=FakeGrindsClient(by_region)
    )


def test_cocoa_reports_each_regions_grind_with_the_year_earlier_tonnes_implied_by_its_change(tmp_path):
    assert _cocoa(tmp_path, GRINDS) == {
        "latest_report": {"name": "ECA and CAA quarterly cocoa grind releases", "date": "2026-10-15"},
        "key_figures": [
            {"figure": "Europe (ECA) cocoa grind (tonnes)", "value": 348215, "prior": 337352, "revision_direction": "up"},
            # Asia's release gives no tonnage, so its year-on-year change is the figure.
            {"figure": "Asia (CAA) cocoa grind (yoy %)", "value": 29.22, "prior": None, "revision_direction": "up"},
        ],
        "stocks_to_use": None,  # there is no cocoa balance sheet
        "demand_indicators": GRINDS,
        "degraded": False,  # the USDA has no cocoa report, which is not a gap
    }


def test_cocoa_with_one_region_missing_keeps_the_other_and_is_degraded(tmp_path):
    result = _cocoa(tmp_path, {"europe": GRINDS["europe"]})

    assert [f["figure"] for f in result["key_figures"]] == ["Europe (ECA) cocoa grind (tonnes)"]
    assert result["demand_indicators"] == {"europe": GRINDS["europe"], "asia": None}
    assert result["degraded"] is True


def test_cocoa_with_no_release_available_has_no_signal(tmp_path):
    result = _cocoa(tmp_path, {})

    assert result == {**_NO_SIGNAL, "demand_indicators": {"europe": None, "asia": None}}


def test_cocoa_without_a_grinds_client_has_no_signal(tmp_path):
    result = compute_supply_demand(tmp_path, commodity="cocoa", as_of=datetime(2026, 10, 16, 6, 0, tzinfo=ET))

    assert result == _NO_SIGNAL

from datetime import datetime

from ags.tools.sources.grinds import GrindsClient, get_grinds
from ags.tools.sources.usda import UsdaClient, get_usda_report, report_for_symbol

_REPORT_NAMES = {"wasde": "WASDE", "coffee_world_markets": "Coffee: World Markets and Trade"}
# Units by commodity: "quantity" covers every figure that is an amount of the crop (stocks, production, use,
# exports), and the rest name the figures that are not.
_GRAIN_UNITS = {"quantity": "million bushels", "yield": "bu/acre", "area_harvested": "million acres", "avg_farm_price": "$/bu"}
_UNITS = {
    "wheat": _GRAIN_UNITS, "corn": _GRAIN_UNITS, "soybeans": _GRAIN_UNITS,
    "cotton": {
        "quantity": "million 480-lb bales", "yield": "lb/acre", "area_harvested": "million acres",
        "avg_farm_price": "cents/lb",
    },
    "sugar": {"quantity": "1,000 short tons"},
    "coffee": {"quantity": "1,000 60-kg bags, world"},
}
# The headline pair, reported first and always; every other figure is reported only when it moved.
_HEADLINE = "ending_stocks"

_DIRECTION = {1: "up", -1: "down", 0: "unchanged"}
# Higher stocks-to-use means a looser balance sheet.
_TREND = {"up": "loosening", "down": "tightening", "unchanged": "unchanged", None: None}


def _revision(value: float, prior: float | None) -> str | None:
    if prior is None:
        return None
    return _DIRECTION[(value > prior) - (value < prior)]


def _unit(commodity: str, figure: str) -> str:
    return _UNITS[commodity].get(figure, _UNITS[commodity]["quantity"])


def _entry(figure: str, value: float, prior: float | None) -> dict:
    return {"figure": figure, "value": value, "prior": prior, "revision_direction": _revision(value, prior)}


_NO_SIGNAL = {
    "latest_report": None, "key_figures": [], "stocks_to_use": None, "demand_indicators": None, "degraded": True,
}


_GRIND_FIGURES = {"europe": "Europe (ECA) cocoa grind", "asia": "Asia (CAA) cocoa grind"}


def _cocoa(data_dir, as_of: datetime, grinds_client: GrindsClient) -> dict:
    """Cocoa has no balance sheet; its demand side is the quarterly grind of each regional association."""
    grinds = get_grinds(data_dir, as_of=as_of, client=grinds_client)
    key_figures = []
    for region, release in grinds.items():
        if release is None:
            continue
        yoy = release["yoy_pct"]
        if release["tonnes"] is None:
            entry = _entry(f"{_GRIND_FIGURES[region]} (yoy %)", yoy, None)
        else:
            entry = _entry(f"{_GRIND_FIGURES[region]} (tonnes)", release["tonnes"], round(release["tonnes"] / (1 + yoy / 100)))
        entry["revision_direction"] = _DIRECTION[(yoy > 0) - (yoy < 0)]
        key_figures.append(entry)
    published = [release["published"] for release in grinds.values() if release]
    return {
        "latest_report": (
            {"name": "ECA and CAA quarterly cocoa grind releases", "date": max(published)} if published else None
        ),
        "key_figures": key_figures,
        "stocks_to_use": None,
        "demand_indicators": grinds,
        "degraded": any(release is None for release in grinds.values()),
    }


def compute_supply_demand(
    data_dir, *, commodity: str, as_of: datetime, usda_client: UsdaClient | None = None,
    grinds_client: GrindsClient | None = None, now: datetime | None = None,
) -> dict:
    """The supply/demand balance from the latest official release, computed by code: no model is involved.

    A figure's prior is last month's forecast of the same marketing year, as the release itself states it,
    or else the earlier release's value when that is the same marketing year. With neither, there is no
    revision and no trend rather than a guess.
    """
    if commodity == "cocoa":
        return _cocoa(data_dir, as_of, grinds_client) if grinds_client is not None else dict(_NO_SIGNAL)
    report = report_for_symbol(commodity)
    try:
        result = get_usda_report(data_dir, report=report, symbol=commodity, as_of=as_of, client=usda_client, now=now)
    except Exception:
        return dict(_NO_SIGNAL)
    latest, earlier = result["latest"], result["prior"]
    if latest is None:
        return dict(_NO_SIGNAL)
    figures = latest.get("figures") or {}

    stocks_prior = figures.get(_HEADLINE, {}).get("prior")
    if stocks_prior is None and earlier:
        stocks_prior = earlier["ending_stocks"]
    ratio_prior = latest.get("prior_stocks_to_use")
    if ratio_prior is None and earlier:
        ratio_prior = earlier["stocks_to_use"]

    key_figures = [
        _entry(f"ending_stocks ({_unit(commodity, 'ending_stocks')})", latest["ending_stocks"], stocks_prior),
        _entry("stocks_to_use (%)", latest["stocks_to_use"], ratio_prior),
    ]
    moved = {
        name: figure for name, figure in figures.items()
        if name != _HEADLINE and figure["prior"] is not None and figure["value"] != figure["prior"]
    }
    for name in sorted(moved, key=lambda n: -abs(moved[n]["value"] / moved[n]["prior"] - 1)):
        key_figures.append(_entry(f"{name} ({_unit(commodity, name)})", moved[name]["value"], moved[name]["prior"]))

    return {
        "latest_report": {"name": _REPORT_NAMES[report], "date": latest["release_date"]},
        "key_figures": key_figures,
        "stocks_to_use": {"value": latest["stocks_to_use"], "trend": _TREND[key_figures[1]["revision_direction"]]},
        "demand_indicators": None,
        "degraded": False,
    }

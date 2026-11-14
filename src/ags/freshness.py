from datetime import date, datetime, timedelta

from ags.tools.pit_store import ET, read_pit, release_instant
from ags.tools.sources import weather

# Older than this and the source has missed a release (drought map weekly, CPC outlook and WASDE monthly,
# cocoa grinds quarterly) or the cycle is reading a stale cache; news is judged on its newest headline.
MAX_AGE_DAYS = {"drought_map": 10, "cpc_outlook": 40, "supply_demand": 40, "cocoa_grinds": 120, "news": 3}


def _age_days(latest: date, as_of: datetime) -> int:
    return (as_of.astimezone(ET).date() - latest).days


def _entry(source: str, latest: date | None, as_of: datetime, detail: str | None = None) -> dict:
    age = None if latest is None else _age_days(latest, as_of)
    limit = MAX_AGE_DAYS[source]
    entry = {
        "source": source, "latest": None if latest is None else latest.isoformat(), "age_days": age,
        "max_age_days": limit, "stale": age is None or age > limit,
    }
    return {**entry, "detail": detail} if detail else entry


def _stored_date(data_dir, source: str, report: str, region: str, field: str, as_of: datetime) -> date | None:
    record = read_pit(data_dir, source=source, report=report, symbol=region, as_of=as_of)
    return None if record is None else date.fromisoformat(record[field])


def source_freshness(data_dir, *, commodity: str, as_of: datetime, outputs: dict) -> list[dict]:
    """Each source's newest release as this cycle saw it, with its age and whether that is older than expected.

    Weather is read from the PIT store for the commodity's US drought-monitor and CPC regions (the oldest
    stored region sets the age); supply/demand and news come from the analysts' outputs. A source with
    nothing to show is stale, not missing from the list.
    """
    out = []
    regions = [region for region in weather.regions_for(commodity) if region in weather._STATES]
    # A commodity with only non-US regions has no drought map or outlook to go stale.
    for source, store, report, field in (
        ("drought_map", weather.SOURCE_DROUGHT, weather.REPORT_DROUGHT, "map_date"),
        ("cpc_outlook", weather.SOURCE_OUTLOOK, weather.REPORT_OUTLOOK, "issued"),
    ):
        if not regions:
            continue
        stored = [_stored_date(data_dir, store, report, region, field, as_of) for region in regions]
        dates = [d for d in stored if d is not None]
        out.append(_entry(source, min(dates) if dates else None, as_of))

    report = (outputs.get("supply_demand") or {}).get("latest_report")
    kind = "cocoa_grinds" if commodity == "cocoa" else "supply_demand"
    latest = date.fromisoformat(report["date"]) if report else None
    out.append(_entry(kind, latest, as_of, report["name"] if report else None))

    news = outputs.get("news") or {}
    dates = [date.fromisoformat(h["date"]) for h in news.get("headlines") or [] if h.get("date")]
    out.append(_entry("news", max(dates) if dates else None, as_of, "carried forward" if news.get("carried_forward") else None))
    return out

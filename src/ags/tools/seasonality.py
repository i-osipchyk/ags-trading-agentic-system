import statistics
from datetime import date, datetime

import pandas as pd

from ags.tools.pit_store import ET


def _week_return(prices: pd.Series, week_closes: pd.Series) -> float | None:
    """The week's return from the close before its first session, so the gap from the prior Friday counts."""
    before = prices[prices.index < week_closes.index[0]]
    if before.empty:
        return None
    return week_closes.iloc[-1] / before.iloc[-1] - 1


def get_seasonality(prices: pd.Series, as_of: date | datetime) -> dict:
    """The as-of ISO week's return in each prior year, and this week's move so far to compare it with.

    `prices` are closed daily closes up to `as_of`. A prior year with no close before its week is skipped.
    """
    day = as_of.astimezone(ET).date() if isinstance(as_of, datetime) else as_of
    iso_year, target_week, _ = day.isocalendar()

    iso = prices.index.isocalendar()
    weeks = pd.DataFrame({"close": prices, "year": iso["year"], "week": iso["week"]})
    same_week = weeks[weeks["week"] == target_week]

    yearly: dict[str, float] = {}
    for year, group in same_week[same_week["year"] < iso_year].groupby("year"):
        value = _week_return(prices, group["close"])
        if value is not None:
            yearly[str(year)] = round(value, 4)

    this_week = same_week[same_week["year"] == iso_year]["close"]
    current = _week_return(prices, this_week) if not this_week.empty else None
    returns = list(yearly.values())
    return {
        "week": target_week,
        "years_observed": len(returns),
        "yearly_returns": yearly,
        "mean_return": round(statistics.mean(returns), 4) if returns else None,
        "median_return": round(statistics.median(returns), 4) if returns else None,
        "stdev_return": round(statistics.stdev(returns), 4) if len(returns) > 1 else None,
        "current_week_return": round(current, 4) if current is not None else None,
        "current_week_sessions": len(this_week),
    }

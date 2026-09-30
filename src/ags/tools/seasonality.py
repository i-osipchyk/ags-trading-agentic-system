import statistics
from datetime import date

import pandas as pd


def get_seasonality(prices: pd.Series, as_of: date) -> dict:
    target_week = as_of.isocalendar().week

    iso = prices.index.isocalendar()
    prior_years_mask = iso["year"] < as_of.year
    same_week_mask = iso["week"] == target_week
    week_prices = prices[prior_years_mask & same_week_mask]

    returns = [
        group.iloc[-1] / group.iloc[0] - 1
        for _, group in week_prices.groupby(week_prices.index.isocalendar()["year"])
    ]

    return {
        "week": target_week,
        "years_observed": len(returns),
        "mean_return": statistics.mean(returns),
        "median_return": statistics.median(returns),
    }

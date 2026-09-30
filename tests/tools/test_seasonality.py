from datetime import date

import pandas as pd
import pytest

from ags.tools.seasonality import get_seasonality


def test_get_seasonality_reports_mean_and_median_return_for_as_of_iso_week_across_prior_years():
    prices = pd.Series(
        {
            # ISO week 5 each year (Feb 1 -> Feb 5): 2021 +10%, 2022 -10%, 2023 +5%
            pd.Timestamp("2021-02-01"): 100.0,
            pd.Timestamp("2021-02-05"): 110.0,
            pd.Timestamp("2022-02-01"): 100.0,
            pd.Timestamp("2022-02-05"): 90.0,
            pd.Timestamp("2023-02-01"): 100.0,
            pd.Timestamp("2023-02-05"): 105.0,
            # distractor: a different ISO week, should never be counted
            pd.Timestamp("2021-02-10"): 500.0,
        }
    ).sort_index()

    result = get_seasonality(prices, as_of=date(2024, 2, 1))

    assert result["week"] == 5
    assert result["years_observed"] == 3
    assert result["mean_return"] == pytest.approx(0.05 / 3)
    assert result["median_return"] == pytest.approx(0.05)

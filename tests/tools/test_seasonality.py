from datetime import datetime
import statistics

import pandas as pd
import pytest

from ags.tools.seasonality import get_seasonality
from ags.tools.pit_store import ET


def _prices(closes: dict[str, float]) -> pd.Series:
    return pd.Series({pd.Timestamp(day): close for day, close in closes.items()}).sort_index()


# ISO week 5 of 2021, 2022, 2023 and 2024, each with the close that ended the week before it.
_PRIOR_YEARS = {
    "2021-01-29": 100.0, "2021-02-05": 110.0,  # +10%
    "2022-01-28": 100.0, "2022-02-04": 90.0,  # -10%
    "2023-01-27": 100.0, "2023-02-03": 105.0,  # +5%
    # a different ISO week, never counted
    "2021-02-10": 500.0,
}
_AS_OF = datetime(2024, 2, 2, 16, 0, tzinfo=ET)  # Friday of ISO week 5, 2024


def test_get_seasonality_reports_each_prior_years_week_return_from_the_previous_weeks_close():
    # The week's return includes the gap from the prior Friday, where weekend news lands.
    prices = _prices({**_PRIOR_YEARS, "2024-01-26": 100.0, "2024-01-29": 102.0, "2024-02-01": 106.0})

    result = get_seasonality(prices, as_of=_AS_OF)

    assert result["week"] == 5
    assert result["years_observed"] == 3
    assert result["yearly_returns"] == {"2021": 0.1, "2022": -0.1, "2023": 0.05}
    assert result["mean_return"] == pytest.approx(0.05 / 3, abs=1e-4)
    assert result["median_return"] == pytest.approx(0.05)
    assert result["stdev_return"] == pytest.approx(statistics.stdev([0.1, -0.1, 0.05]), abs=1e-4)


def test_get_seasonality_reports_this_weeks_move_so_far_for_comparison():
    prices = _prices({**_PRIOR_YEARS, "2024-01-26": 100.0, "2024-01-29": 102.0, "2024-02-01": 106.0})

    result = get_seasonality(prices, as_of=_AS_OF)

    assert result["current_week_return"] == pytest.approx(0.06)
    assert result["current_week_sessions"] == 2


def test_get_seasonality_has_no_current_move_before_the_weeks_first_session_has_closed():
    prices = _prices({**_PRIOR_YEARS, "2024-01-26": 100.0, "2024-02-02": 107.0})

    result = get_seasonality(prices, as_of=datetime(2024, 2, 5, 6, 0, tzinfo=ET))  # Monday of week 6, 06:00

    assert result["week"] == 6
    assert result["current_week_return"] is None
    assert result["current_week_sessions"] == 0


def test_get_seasonality_skips_a_year_with_no_close_before_its_week():
    prices = _prices({"2022-02-01": 100.0, "2022-02-04": 90.0, "2023-01-27": 100.0, "2023-02-03": 105.0})

    result = get_seasonality(prices, as_of=_AS_OF)

    assert result["yearly_returns"] == {"2023": 0.05}  # nothing precedes 2022's first bar


def test_get_seasonality_with_no_prior_years_has_no_statistics_instead_of_raising():
    result = get_seasonality(_prices({"2024-01-26": 100.0, "2024-01-29": 102.0}), as_of=_AS_OF)

    assert result["years_observed"] == 0
    assert result["mean_return"] is None and result["median_return"] is None and result["stdev_return"] is None


def test_get_seasonality_has_no_spread_from_a_single_year():
    prices = _prices({"2023-01-27": 100.0, "2023-02-03": 105.0})

    result = get_seasonality(prices, as_of=_AS_OF)

    assert result["years_observed"] == 1
    assert result["stdev_return"] is None

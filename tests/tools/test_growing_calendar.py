from datetime import date

from ags.tools.growing_calendar import get_growing_calendar


def test_get_growing_calendar_reports_corn_flowering_in_july():
    # Corn silking/pollination (R1 stage) in the US Corn Belt is a
    # well-documented mid-to-late-July event — an independent agronomic
    # fact, not something derived from the lookup table itself.
    result = get_growing_calendar("corn", as_of=date(2026, 7, 15))

    assert result == {"stage": "flowering"}


def test_get_growing_calendar_reports_corn_harvest_in_october():
    result = get_growing_calendar("corn", as_of=date(2026, 10, 15))

    assert result == {"stage": "harvest"}


def test_get_growing_calendar_reports_us_winter_wheat_dormant_in_december():
    # US winter wheat (the majority of US wheat acreage) is fall-planted
    # and overwinters dormant until spring green-up — an independent,
    # well-documented agronomic fact.
    result = get_growing_calendar("wheat", as_of=date(2026, 12, 15))

    assert result == {"stage": "dormant"}

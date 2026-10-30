from datetime import date, datetime

from ags.tools.growing_calendar import get_growing_calendar
from ags.tools.pit_store import ET


def test_get_growing_calendar_reports_corn_flowering_in_july():
    # Corn silking/pollination (R1 stage) in the US Corn Belt is a
    # well-documented mid-to-late-July event — an independent agronomic
    # fact, not something derived from the lookup table itself.
    result = get_growing_calendar("corn", as_of=datetime(2026, 7, 15, 16, 0, tzinfo=ET))

    assert result == {"stage": "flowering"}


def test_get_growing_calendar_reports_corn_harvest_in_october():
    result = get_growing_calendar("corn", as_of=datetime(2026, 10, 15, 16, 0, tzinfo=ET))

    assert result == {"stage": "harvest"}


def test_get_growing_calendar_reports_us_winter_wheat_dormant_in_december():
    # US winter wheat (the majority of US wheat acreage) is fall-planted
    # and overwinters dormant until spring green-up — an independent,
    # well-documented agronomic fact.
    result = get_growing_calendar("wheat", as_of=datetime(2026, 12, 15, 16, 0, tzinfo=ET))

    assert result == {"stage": "dormant"}


def test_get_growing_calendar_follows_the_brazil_center_south_cane_crush_for_sugar():
    # ICE No.11 is priced off Brazil's Center-South, whose crush runs roughly April to November and peaks
    # mid-year (UNICA reports weekly crush through that season); January-March is the growing season.
    assert get_growing_calendar("sugar", as_of=datetime(2026, 7, 15, 16, 0, tzinfo=ET)) == {"stage": "harvest"}
    assert get_growing_calendar("sugar", as_of=datetime(2026, 1, 15, 16, 0, tzinfo=ET)) == {"stage": "vegetative"}

from datetime import date

import pytest

from ags.tools.sources import usda
from ags.tools.sources.usda import EsmisClient, get_usda_report


@pytest.mark.integration
@pytest.mark.parametrize("symbol", ["corn", "soybeans", "wheat"])
def test_get_usda_report_pulls_real_wasde_from_esmis_and_is_pit_safe(tmp_path, symbol):
    client = EsmisClient()
    as_of = date(2026, 9, 20)

    result = get_usda_report(tmp_path, report="wasde", symbol=symbol, as_of=as_of, client=client)

    # Sep 2026 WASDE was released 2026-09-11; the 2026-10 one must not leak.
    assert result["latest"]["release_date"] == "2026-09-11"
    assert result["latest"]["ending_stocks"] > 0
    assert 0 < result["latest"]["stocks_to_use"] < 100
    assert result["prior"]["release_date"] == "2026-08-12"
    assert result["revision_direction"] in {"up", "down", "unchanged"}
    assert list((tmp_path / usda.SOURCE / "wasde" / symbol).glob("*.json"))

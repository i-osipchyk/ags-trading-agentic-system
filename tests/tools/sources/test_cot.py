import json
from datetime import date, datetime, timedelta

from ags.tools.pit_store import ET
from ags.tools.sources import cot
from ags.tools.sources.cot import SocrataCotClient, get_positioning


class FakeCotClient:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def get_reports(self, symbol, *, start, end):
        self.calls.append((symbol, start, end))
        return list(self._rows)


def _row(report_date: date, open_interest: int, long: int, short: int) -> dict:
    return {
        "report_date": report_date.isoformat(),
        "open_interest": open_interest,
        "managed_money_long": long,
        "managed_money_short": short,
    }


def _coffee_history() -> list[dict]:
    # Latest report is the real coffee C row of Tue 2026-10-20 (open interest 170,911; managed money long
    # 38,596, short 16,259). The 155 weeks before it have managed-money net of exactly 1%, 2%, ... 155% of
    # open interest, newest first.
    rows = [_row(date(2026, 10, 20), 170911, 38596, 16259)]
    for weeks_back in range(1, 156):
        net_pct = weeks_back
        rows.append(_row(date(2026, 10, 20) - timedelta(weeks=weeks_back), 1000, 10 * net_pct, 0))
    return rows


def test_get_positioning_reports_managed_money_net_and_its_three_year_percentile(tmp_path):
    client = FakeCotClient(_coffee_history())

    result = get_positioning(tmp_path, symbol="coffee", as_of=datetime(2026, 10, 23, 16, 0, tzinfo=ET), client=client)

    # Net 22,337 lots = 13.07% of open interest; 13 of the 155 earlier weeks (1%..13%) sit at or below it,
    # plus the week itself: 14 of 156 = 8.97th percentile.
    assert result == {
        "report_date": "2026-10-20",
        "released": "2026-10-23",
        "managed_money_net": 22337,
        "open_interest": 170911,
        "net_pct_oi": 13.07,
        "percentile": 8.97,
        "weeks": 156,
    }


def test_get_positioning_only_uses_reports_public_at_as_of(tmp_path):
    rows = _coffee_history() + [_row(date(2026, 10, 27), 175000, 30000, 20000)]
    client = FakeCotClient(rows)

    before_release = get_positioning(
        tmp_path, symbol="coffee", as_of=datetime(2026, 10, 30, 12, 30, tzinfo=ET), client=client
    )
    after_release = get_positioning(
        tmp_path, symbol="coffee", as_of=datetime(2026, 10, 30, 16, 0, tzinfo=ET), client=client
    )

    # The Oct 27 report comes out Friday Oct 30 at 15:30 ET: the 06:00 and 12:30 cycles must not see it.
    assert before_release["report_date"] == "2026-10-20"
    assert after_release["report_date"] == "2026-10-27"
    assert after_release["managed_money_net"] == 10000


def test_get_positioning_archives_each_release_once(tmp_path):
    rows = _coffee_history() + [_row(date(2026, 10, 27), 175000, 30000, 20000)]
    client = FakeCotClient(rows)

    friday_pm = get_positioning(tmp_path, symbol="coffee", as_of=datetime(2026, 10, 23, 16, 0, tzinfo=ET), client=client)
    monday = get_positioning(tmp_path, symbol="coffee", as_of=datetime(2026, 10, 26, 6, 0, tzinfo=ET), client=client)
    next_friday = get_positioning(
        tmp_path, symbol="coffee", as_of=datetime(2026, 10, 30, 16, 0, tzinfo=ET), client=client
    )

    archived = sorted((tmp_path / "cftc_cot" / "managed_money" / "coffee").glob("*.json"))
    assert [json.loads(path.read_text()) for path in archived] == [friday_pm, next_friday]
    assert monday == friday_pm


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_socrata_client_maps_the_disaggregated_futures_rows_of_the_symbol_s_contract(monkeypatch):
    # Rows exactly as publicreporting.cftc.gov returns them: every number a string, dates with a time part.
    payload = [
        {
            "report_date_as_yyyy_mm_dd": "2026-10-20T00:00:00.000",
            "open_interest_all": "170911",
            "m_money_positions_long_all": "38596",
            "m_money_positions_short_all": "16259",
        },
        {
            "report_date_as_yyyy_mm_dd": "2026-10-13T00:00:00.000",
            "open_interest_all": "163626",
            "m_money_positions_long_all": "35096",
            "m_money_positions_short_all": "19826",
        },
    ]
    requests_made = []

    def fake_get(url, **kwargs):
        requests_made.append((url, kwargs["params"]))
        return _Response(payload)

    monkeypatch.setattr(cot.requests, "get", fake_get)

    rows = SocrataCotClient().get_reports("coffee", start=date(2023, 10, 1), end=date(2026, 10, 23))

    assert rows == [
        {"report_date": "2026-10-20", "open_interest": 170911, "managed_money_long": 38596, "managed_money_short": 16259},
        {"report_date": "2026-10-13", "open_interest": 163626, "managed_money_long": 35096, "managed_money_short": 19826},
    ]
    url, params = requests_made[0]
    assert url == "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # disaggregated, futures only
    assert "083731" in params["$where"]  # Coffee C

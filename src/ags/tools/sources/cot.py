from datetime import date, datetime, time, timedelta
from typing import Protocol

import requests

from ags.tools.pit_store import ET, write_pit

SOURCE = "cftc_cot"
REPORT = "managed_money"

# Weeks of managed-money history a positioning percentile is ranked against (three years).
PERCENTILE_WEEKS = 156

# The Tuesday report is published the Friday after, 15:30 ET.
_RELEASE_LAG = timedelta(days=3)
_RELEASE_TIME = time(15, 30)


_SOCRATA_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # disaggregated, futures only

# CFTC contract market codes of the traded basket (corn, soybeans and wheat are the CBOT contracts).
_CONTRACT_CODES = {
    "corn": "002602",
    "soybeans": "005602",
    "wheat": "001602",
    "sugar": "080732",
    "coffee": "083731",
    "cotton": "033661",
    "cocoa": "073732",
}


class CotClient(Protocol):
    def get_reports(self, symbol: str, *, start: date, end: date) -> list[dict]: ...


class SocrataCotClient:
    """CFTC Public Reporting Environment (publicreporting.cftc.gov), disaggregated futures-only COT."""

    def get_reports(self, symbol: str, *, start: date, end: date) -> list[dict]:
        response = requests.get(
            _SOCRATA_URL,
            params={
                "$where": (
                    f"cftc_contract_market_code='{_CONTRACT_CODES[symbol]}' "
                    f"AND report_date_as_yyyy_mm_dd between '{start.isoformat()}T00:00:00' "
                    f"and '{end.isoformat()}T23:59:59'"
                ),
                "$select": "report_date_as_yyyy_mm_dd,open_interest_all,m_money_positions_long_all,m_money_positions_short_all",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": 1000,
            },
            timeout=30,
        )
        response.raise_for_status()
        return [
            {
                "report_date": row["report_date_as_yyyy_mm_dd"][:10],
                "open_interest": int(row["open_interest_all"]),
                "managed_money_long": int(row["m_money_positions_long_all"]),
                "managed_money_short": int(row["m_money_positions_short_all"]),
            }
            for row in response.json()
        ]


def _release_instant(report_date: date) -> datetime:
    return datetime.combine(report_date + _RELEASE_LAG, _RELEASE_TIME, tzinfo=ET)


def _net_pct_oi(row: dict) -> float:
    return (row["managed_money_long"] - row["managed_money_short"]) / row["open_interest"] * 100


def get_positioning(data_dir, *, symbol: str, as_of: datetime, client: CotClient) -> dict | None:
    """Managed-money net position (CFTC disaggregated COT, futures only) of the latest report public at `as_of`."""
    day = as_of.astimezone(ET).date()
    start = day - timedelta(weeks=PERCENTILE_WEEKS + 2)
    public = [
        row
        for row in client.get_reports(symbol, start=start, end=day)
        if _release_instant(date.fromisoformat(row["report_date"])) <= as_of
    ]
    if not public:
        return None
    window = sorted(public, key=lambda row: row["report_date"])[-PERCENTILE_WEEKS:]
    latest = window[-1]
    report_date = date.fromisoformat(latest["report_date"])
    net_pct = _net_pct_oi(latest)
    at_or_below = sum(1 for row in window if _net_pct_oi(row) <= net_pct)
    record = {
        "report_date": report_date.isoformat(),
        "released": _release_instant(report_date).date().isoformat(),
        "managed_money_net": latest["managed_money_long"] - latest["managed_money_short"],
        "open_interest": latest["open_interest"],
        "net_pct_oi": round(net_pct, 2),
        "percentile": round(at_or_below / len(window) * 100, 2),
        "weeks": len(window),
    }
    # Fetched every cycle (the cache of a weekly source must not outlive its next release); each release is
    # archived once under its own release instant.
    write_pit(data_dir, source=SOURCE, report=REPORT, symbol=symbol, release_date=_release_instant(report_date), content=record)
    return record

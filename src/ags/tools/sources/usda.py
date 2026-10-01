import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Protocol

import requests

from ags.tools.pit_store import read_pit, write_pit

SOURCE = "usda_esmis"
_FIGURES = ("ending_stocks", "stocks_to_use")


_ESMIS_BASE = "https://esmis.nal.usda.gov"
_WASDE_LISTING = _ESMIS_BASE + "/publication/world-agricultural-supply-and-demand-estimates"
_MAX_LISTING_PAGES = 10

# (sub-report, matrix index) of each commodity's U.S. supply/use table in the
# WASDE XML; all three are in million bushels (wheat, corn, soybeans).
_WASDE_TABLES = {"wheat": ("sr11", 0), "corn": ("sr12", 1), "soybeans": ("sr15", 0)}

_RELEASE_RE = re.compile(
    r'href="(/sites/default/release-files/\d+/wasde[^"]*\.xml)"[^>]*>\s*<span[^>]*>\s*(?:[^<]*?)<time datetime="(\d{4}-\d{2}-\d{2})'
)


class UsdaClient(Protocol):
    def get_report(self, report: str, symbol: str, *, as_of: date) -> dict | None: ...


def _row_cells(row: ET.Element) -> list[tuple[str, float]]:
    cells = []
    for year_group in row.iter():
        if not year_group.tag.endswith("year_group"):
            continue
        year = next(v for k, v in year_group.attrib.items() if k.startswith("market_year"))
        for cell in year_group.iter("Cell"):
            value = next((v for k, v in cell.attrib.items() if k.startswith("cell_value")), None)
            if value:
                cells.append((year, float(value.replace(",", ""))))
    return cells


def _parse_wasde(xml_bytes: bytes, symbol: str) -> dict | None:
    sub_report, matrix_idx = _WASDE_TABLES[symbol]
    root = ET.fromstring(xml_bytes.lstrip(b"\xef\xbb\xbf"))
    report = root.find(sub_report).find("Report")
    matrices = [m for m in report if m.tag.startswith("matrix")]

    figures = {}
    for element in matrices[matrix_idx].iter():
        attr = next((v for k, v in element.attrib.items() if re.fullmatch(r"attribute\d+", k)), None)
        if attr is None:
            continue
        name = " ".join(attr.split()).lower()
        if name in ("ending stocks", "use, total") and _row_cells(element):
            # The last cell is the newest marketing year's most recent forecast month.
            figures[name] = _row_cells(element)[-1]

    if "ending stocks" not in figures or "use, total" not in figures:
        return None
    year, ending = figures["ending stocks"]
    _, use = figures["use, total"]
    return {
        "marketing_year": year,
        "ending_stocks": ending,
        "stocks_to_use": round(ending / use * 100, 2),
    }


class EsmisClient:
    """USDA ESMIS archive of as-released WASDE XML (esmis.nal.usda.gov) — not PSD Online."""

    def _releases(self, page: int) -> list[tuple[date, str]]:
        response = requests.get(_WASDE_LISTING, params={"page": page}, timeout=30)
        response.raise_for_status()
        seen = {}
        for path, day in _RELEASE_RE.findall(response.text):
            seen[path] = date.fromisoformat(day)
        return [(d, p) for p, d in seen.items()]

    def get_report(self, report: str, symbol: str, *, as_of: date) -> dict | None:
        if report != "wasde" or symbol not in _WASDE_TABLES:
            return None

        for page in range(_MAX_LISTING_PAGES):
            releases = self._releases(page)
            if not releases:
                return None
            eligible = [r for r in releases if r[0] <= as_of]
            if not eligible:
                continue
            release_date, path = max(eligible)
            response = requests.get(_ESMIS_BASE + path, timeout=120)
            response.raise_for_status()
            parsed = _parse_wasde(response.content, symbol)
            if parsed is None:
                return None
            return {"release_date": release_date.isoformat(), **parsed}
        return None


def _direction(delta: float) -> str:
    return "up" if delta > 0 else "down" if delta < 0 else "unchanged"


def _get_release(data_dir, *, report: str, symbol: str, as_of: date, client: UsdaClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE, report=report, symbol=symbol, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_report(report, symbol, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    release_date = date.fromisoformat(record["release_date"])
    if release_date > as_of:
        return None

    write_pit(data_dir, source=SOURCE, report=report, symbol=symbol, release_date=release_date, content=record)
    return record


def get_usda_report(data_dir, *, report: str, symbol: str, as_of: date, client: UsdaClient) -> dict:
    latest = _get_release(data_dir, report=report, symbol=symbol, as_of=as_of, client=client)
    result = {"report": report, "symbol": symbol, "latest": latest, "prior": None}
    if latest is None:
        return result

    day_before_latest = date.fromisoformat(latest["release_date"]) - timedelta(days=1)
    prior = _get_release(data_dir, report=report, symbol=symbol, as_of=day_before_latest, client=client)
    result["prior"] = prior
    if prior is not None:
        change = {k: round(latest[k] - prior[k], 4) for k in _FIGURES}
        result["change"] = change
        result["revision_direction"] = _direction(change["ending_stocks"])
    return result


def get_balance_sheet_revisions(data_dir, *, symbol: str, as_of: date, client: UsdaClient) -> dict:
    result = get_usda_report(data_dir, report="wasde", symbol=symbol, as_of=as_of, client=client)
    change = result.get("change")
    if change is None:
        return {"symbol": symbol, "ending_stocks_change": None, "stocks_to_use_change": None, "stocks_to_use_trend": None}

    delta = change["stocks_to_use"]
    return {
        "symbol": symbol,
        "ending_stocks_change": change["ending_stocks"],
        "stocks_to_use_change": delta,
        "stocks_to_use_trend": "loosening" if delta > 0 else "tightening" if delta < 0 else "unchanged",
    }

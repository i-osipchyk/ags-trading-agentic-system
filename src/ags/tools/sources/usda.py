import io
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Protocol

import requests
from pypdf import PdfReader

from ags.tools.pit_store import ET as EASTERN, is_released, read_pit, release_instant, write_pit

SOURCE = "usda_esmis"
_FIGURES = ("ending_stocks", "stocks_to_use")


_ESMIS_BASE = "https://esmis.nal.usda.gov"
_WASDE_LISTING = _ESMIS_BASE + "/publication/world-agricultural-supply-and-demand-estimates"
_MAX_LISTING_PAGES = 10

# (sub-report, matrix index) of each commodity's U.S. supply/use table in the
# WASDE XML. Units differ per commodity (million bushels for wheat/corn/soybeans,
# million bales for cotton, 1,000 short tons for sugar); only stocks-to-use is comparable across them.
_WASDE_TABLES = {"wheat": ("sr11", 0), "corn": ("sr12", 1), "soybeans": ("sr15", 0), "cotton": ("sr17", 0), "sugar": ("sr16", 0)}

# The sugar table labels its total-use row differently from the grain/cotton tables.
_TOTAL_USE_ALIASES = {"total use": "use, total"}

_RELEASE_RE = re.compile(
    r'href="(/sites/default/release-files/\d+/wasde[^"]*\.xml)"[^>]*>\s*<span[^>]*>\s*(?:[^<]*?)<time datetime="(\d{4}-\d{2}-\d{2})'
)


# FAS "Coffee: World Markets and Trade" — semiannual PDF, world totals only. ESMIS stopped carrying it after
# Jun 2025; FAS serves every edition from apps.fas.usda.gov, where .../YYYY/MM/coffee.pdf is the latest edition
# released in or before that month, so the release date comes from the PDF itself.
_FAS_CIRCULARS = "https://apps.fas.usda.gov/psdonline/circulars"
_COFFEE_LOOKBACK_MONTHS = 14  # editions are 6 months apart; the extra months cover a late or missed one
_YEAR_RE = re.compile(r"\b\d{4}/\d{2}\b")
_TOTAL_ROW_RE = re.compile(r"^\s*[\d,]*\s*Total\s+([\d,\s]+)$")
# Section labels in the balance tables read "Domestic Consumption none" / "Ending Stocks none"; body text and
# headlines ("World Ending Stocks Continue to Tighten") mention the same words and must not switch sections.
_SECTION_RE = re.compile(r"^\s*(Domestic Consumption|Ending Stocks)\s+none\s*$")
_SECTION_BY_LABEL = {"Domestic Consumption": "use", "Ending Stocks": "ending_stocks"}


# The report each commodity's balance sheet comes from; WASDE unless listed.
_REPORT_BY_SYMBOL = {"coffee": "coffee_world_markets"}


def report_for_symbol(symbol: str) -> str:
    return _REPORT_BY_SYMBOL.get(symbol, "wasde")


class UsdaClient(Protocol):
    def get_report(self, report: str, symbol: str, *, as_of: datetime) -> dict | None: ...


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
        name = _TOTAL_USE_ALIASES.get(name, name)
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


def _pdf_release_date(pdf_bytes: bytes) -> date:
    return PdfReader(io.BytesIO(pdf_bytes)).metadata.creation_date.date()


def _parse_coffee(pdf_bytes: bytes) -> dict | None:
    lines = []
    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        lines.extend((page.extract_text() or "").splitlines())

    marketing_year = None
    section = None
    totals = {}
    for line in lines:
        if label := _SECTION_RE.match(line):
            section = _SECTION_BY_LABEL[label.group(1)]
        elif section is None:
            years = _YEAR_RE.findall(line)
            if years:
                marketing_year = years[-1]
        elif section not in totals and (match := _TOTAL_ROW_RE.match(line)):
            # The newest marketing year is the last column of the world Total row.
            totals[section] = float(match.group(1).split()[-1].replace(",", ""))

    if marketing_year is None or "use" not in totals or "ending_stocks" not in totals:
        return None
    return {
        "marketing_year": marketing_year,
        "ending_stocks": totals["ending_stocks"],
        "stocks_to_use": round(totals["ending_stocks"] / totals["use"] * 100, 2),
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

    def _coffee_release(self, as_of: datetime) -> dict | None:
        local = as_of.astimezone(EASTERN)
        year, month = local.year, local.month
        for _ in range(_COFFEE_LOOKBACK_MONTHS):
            response = requests.get(f"{_FAS_CIRCULARS}/{year}/{month:02d}/coffee.pdf", timeout=120)
            response.raise_for_status()
            release_date = _pdf_release_date(response.content)
            if is_released(SOURCE, release_date, as_of):
                parsed = _parse_coffee(response.content)
                return None if parsed is None else {"release_date": release_date.isoformat(), **parsed}
            # The folder already serves an edition newer than `as_of`; an earlier month serves the one before.
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        return None

    def get_report(self, report: str, symbol: str, *, as_of: datetime) -> dict | None:
        if report == "coffee_world_markets" and symbol == "coffee":
            return self._coffee_release(as_of)
        if report != "wasde" or symbol not in _WASDE_TABLES:
            return None

        for page in range(_MAX_LISTING_PAGES):
            releases = self._releases(page)
            if not releases:
                return None
            eligible = [r for r in releases if is_released(SOURCE, r[0], as_of)]
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


def _get_release(data_dir, *, report: str, symbol: str, as_of: datetime, client: UsdaClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE, report=report, symbol=symbol, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_report(report, symbol, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    release_date = date.fromisoformat(record["release_date"])
    if not is_released(SOURCE, release_date, as_of):
        return None

    write_pit(data_dir, source=SOURCE, report=report, symbol=symbol, release_date=release_date, content=record)
    return record


def get_usda_report(data_dir, *, report: str, symbol: str, as_of: datetime, client: UsdaClient) -> dict:
    latest = _get_release(data_dir, report=report, symbol=symbol, as_of=as_of, client=client)
    result = {"report": report, "symbol": symbol, "latest": latest, "prior": None}
    if latest is None:
        return result

    just_before_latest = release_instant(SOURCE, date.fromisoformat(latest["release_date"])) - timedelta(microseconds=1)
    prior = _get_release(data_dir, report=report, symbol=symbol, as_of=just_before_latest, client=client)
    result["prior"] = prior
    if prior is not None:
        change = {k: round(latest[k] - prior[k], 4) for k in _FIGURES}
        result["change"] = change
        result["revision_direction"] = _direction(change["ending_stocks"])
    return result


def get_balance_sheet_revisions(data_dir, *, symbol: str, as_of: datetime, client: UsdaClient) -> dict:
    result = get_usda_report(
        data_dir, report=report_for_symbol(symbol), symbol=symbol, as_of=as_of, client=client
    )
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

import io
import re
from datetime import date, datetime
from typing import Protocol

import requests
from pypdf import PdfReader

from ags.tools.pit_store import is_released, write_pit

SOURCE = "cocoa_grinds"
REPORT = "quarterly"

# North America (NCA) is parsed by the client but not requested: candyusa.com answers automated clients with
# HTTP 403 (and its REST API with 401). Add "north_america" back once access is arranged.
REGIONS = ("europe", "asia")


_NCA_LISTING = "https://candyusa.com/cocoa-grinds-report/"
# Grind reports only: the page also links a revised letter and the release schedule, which carry a quarter too.
_NCA_REPORT_RE = re.compile(
    r'href="(?P<url>https://candyusa\.com/[^"]*?Q(?P<quarter>[1-4])[-_](?P<year>\d{4})[-_]?Cocoa[-_]?Grinds[^"]*\.pdf)"'
)
_ECA_LISTING = "https://www.eurococoa.com/grind-stats/"
_ECA_REPORT_RE = re.compile(
    r'href="(?P<url>https://www\.eurococoa\.com/wp-content/uploads/[^"]*WESTERN-STATS-Q(?P<quarter>[1-4])-(?P<year>\d{4})[^"]*\.pdf)"'
)
_CAA_LISTING = "https://www.cocoaasia.org/grinding-figures"
# A quarter's label is followed by its download link (a PDF, or for some quarters only an image).
_CAA_REPORT_RE = re.compile(
    r'(?P<year>\d{4}) Q(?P<quarter>[1-4])(?:(?!\d{4} Q[1-4]).){0,3000}?href="(?P<url>https://www\.cocoaasia\.org/_files/ugd/[^"]+\.pdf)"',
    re.S,
)
_CAA_DATE_RE = re.compile(r"Published:\s*(\d{1,2})(?:st|nd|rd|th)? ([A-Za-z]+) (\d{4})")
_CAA_YOY_RE = re.compile(r"(Increase|Decrease) of ([+-]?\d+(?:\.\d+)?)% compared to the corresponding period last year")
_ECA_DATE_RE = re.compile(r"Date:\s*([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)? (\d{4})")
_ECA_YEAR_ROW_RE = re.compile(r"^(\d{4}) ([\d, ]+)$")
_QUARTER_BY_WORD = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}
_NCA_SUBJECT_RE = re.compile(r"Release of (First|Second|Third|Fourth) Quarter Cocoa Grindings for (\d{4})")
_NCA_DATE_RE = re.compile(r"Date:\s*([A-Za-z]+ \d{1,2}, \d{4})")
# "Cocoa Beans Ground  109,978  112, 784  (2,806)  (2.49%)": this year, last year, change, change %. The PDF text
# has stray spaces inside numbers, and a decrease is shown in parentheses.
_NCA_ROW_RE = re.compile(
    r"Cocoa Beans Ground\s+(\d{1,3}(?:,\s*\d{3})*)\s+\d{1,3}(?:,\s*\d{3})*\s+\(?[\d,\s]+\)?\s+(\()?\s*(\d+\.\s*\d+)%"
)


class GrindsClient(Protocol):
    def get_release(self, region: str, *, as_of: datetime) -> dict | None: ...


def _pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


def _number(text: str) -> float:
    return float(re.sub(r"[,\s]", "", text))


def _parse_nca(content: bytes) -> dict | None:
    text = _pdf_text(content)
    subject, posted, row = _NCA_SUBJECT_RE.search(text), _NCA_DATE_RE.search(text), _NCA_ROW_RE.search(text)
    if not (subject and posted and row):
        return None
    change = _number(row.group(3))
    return {
        "quarter": f"{subject.group(2)}-Q{_QUARTER_BY_WORD[subject.group(1)]}",
        "published": datetime.strptime(posted.group(1), "%B %d, %Y").date().isoformat(),
        "tonnes": int(_number(row.group(1))),
        "yoy_pct": -change if row.group(2) else change,
    }


def _parse_eca(content: bytes) -> dict | None:
    text = _pdf_text(content)
    posted = _ECA_DATE_RE.search(text)
    if posted is None or "Quarterly Results" not in text:
        return None
    # Rows of "Quarterly Results" list Q4 Q3 Q2 Q1 then the year to date and the 12-month total, but the year
    # in progress leaves out the quarters not reported yet, so its first number is its latest quarter.
    rows = {}
    for line in text.split("Quarterly Results", 1)[1].splitlines():
        match = _ECA_YEAR_ROW_RE.match(line.strip())
        if match and match.group(1) not in rows:
            rows[match.group(1)] = [int(_number(value)) for value in match.group(2).replace(", ", ",").split()]
    year = max(rows)
    latest, previous = rows[year], rows[str(int(year) - 1)]
    quarter = len(latest) - 2
    tonnes, prior_year = latest[0], previous[4 - quarter]
    return {
        "quarter": f"{year}-Q{quarter}",
        "published": datetime.strptime(f"{posted.group(1)} {posted.group(2)} {posted.group(3)}", "%B %d %Y").date().isoformat(),
        "tonnes": tonnes,
        "yoy_pct": round((tonnes / prior_year - 1) * 100, 2),
    }


def _parse_caa(content: bytes, quarter: str) -> dict | None:
    text = _pdf_text(content)
    posted, yoy = _CAA_DATE_RE.search(text), _CAA_YOY_RE.search(text)
    if posted is None or yoy is None:
        return None
    change = abs(float(yoy.group(2)))
    return {
        "quarter": quarter,
        "published": datetime.strptime(f"{posted.group(1)} {posted.group(2)} {posted.group(3)}", "%d %B %Y").date().isoformat(),
        "tonnes": None,  # only in the chart text, not parsed
        "yoy_pct": -change if yoy.group(1) == "Decrease" else change,
    }


class CocoaGrindsClient:
    """The quarterly grind releases of the three regional cocoa associations."""

    def get_release(self, region: str, *, as_of: datetime) -> dict | None:
        listing, pattern, parse = {
            "north_america": (_NCA_LISTING, _NCA_REPORT_RE, lambda content, quarter: _parse_nca(content)),
            "europe": (_ECA_LISTING, _ECA_REPORT_RE, lambda content, quarter: _parse_eca(content)),
            "asia": (_CAA_LISTING, _CAA_REPORT_RE, _parse_caa),
        }[region]
        return self._latest_released(listing, pattern, parse, as_of)

    def _latest_released(self, listing_url: str, pattern: re.Pattern, parse, as_of: datetime) -> dict | None:
        listing = requests.get(listing_url, timeout=30)
        listing.raise_for_status()
        reports = sorted(
            {(int(m["year"]), int(m["quarter"]), m["url"]) for m in pattern.finditer(listing.text)}, reverse=True
        )
        for year, quarter, url in reports:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            record = parse(response.content, f"{year}-Q{quarter}")
            if record is not None and is_released(SOURCE, date.fromisoformat(record["published"]), as_of):
                return record
        return None


def get_grinds(data_dir, *, as_of: datetime, client: GrindsClient) -> dict:
    """Latest quarterly cocoa grind release of each region (Europe: ECA, North America: NCA, Asia: CAA)."""
    result = {}
    for region in REGIONS:
        try:
            record = _released(client.get_release(region, as_of=as_of), as_of)
        except Exception:
            # One body's site being down or changing layout must not cost the other regions.
            record = None
        if record is not None:
            # Releases are quarterly and can be revised later, so each is kept as first published.
            write_pit(
                data_dir, source=SOURCE, report=REPORT, symbol=region,
                release_date=date.fromisoformat(record["published"]), content=record,
            )
        result[region] = record
    return result


def _released(record: dict | None, as_of: datetime) -> dict | None:
    # Defensive PIT filter: the client's own date-scoping is a request, not a guarantee. A date-only release
    # counts as public from the end of its day (the three bodies publish at different hours).
    if record is None or not is_released(SOURCE, date.fromisoformat(record["published"]), as_of):
        return None
    return record

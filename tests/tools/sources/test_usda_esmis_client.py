import re
from datetime import date, datetime

from ags.tools.sources import usda
from ags.tools.sources.usda import EsmisClient
from ags.tools.pit_store import ET

_LISTING_HTML = (
    '<a href="/sites/default/release-files/796054/wasde0926.xml" class="x">'
    '<span class="y"> <time datetime="2026-09-11T12:00:00Z">Sep 11</time>'
)


def _row(name: str, value: str, tag: str = "attribute4") -> str:
    return (
        f'<{tag} {tag}="  {name}"><m1_year_group_Collection>'
        f'<m1_year_group market_year4="2026/27 Proj."><m1_month_group_Collection>'
        f'<m1_month_group forecast_month4="Sep"><Cell cell_value4="{value}" />'
        f"</m1_month_group></m1_month_group_Collection></m1_year_group>"
        f"</m1_year_group_Collection></{tag}>"
    )


def _wasde_xml(sub_report: str, rows: list[tuple[str, str]]) -> bytes:
    body = "".join(_row(name, value) for name, value in rows)
    return (
        f"<WASDE><{sub_report}><Report><matrix1><m1_attribute_group_Collection>"
        f"<m1_attribute_group>{body}</m1_attribute_group></m1_attribute_group_Collection>"
        f"</matrix1></Report></{sub_report}></WASDE>"
    ).encode()


class _Response:
    def __init__(self, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _serve(monkeypatch, xml: bytes):
    def fake_get(url, **kwargs):
        if url.endswith(".xml"):
            return _Response(content=xml)
        return _Response(text=_LISTING_HTML)

    monkeypatch.setattr(usda.requests, "get", fake_get)


def test_esmis_client_parses_cotton_from_sep_2026_wasde(monkeypatch):
    # Figures from the real Sep 2026 WASDE, U.S. Cotton Supply and Use (million bales).
    _serve(
        monkeypatch,
        _wasde_xml("sr17", [("Planted", "10.45 **"), ("Use, Total", "13.80"), ("Ending Stocks", "3.60")]),
    )

    record = EsmisClient().get_report("wasde", "cotton", as_of=datetime(2026, 9, 20, 16, 0, tzinfo=ET))

    assert record == {
        "release_date": "2026-09-11",
        "marketing_year": "2026/27 Proj.",
        "ending_stocks": 3.6,
        "stocks_to_use": 26.09,
    }


def test_esmis_client_parses_sugar_from_sep_2026_wasde(monkeypatch):
    # Figures from the real Sep 2026 WASDE, U.S. Sugar Supply and Use (1,000 short tons, raw value);
    # the sugar table labels total use "Total Use" rather than "Use, Total".
    _serve(
        monkeypatch,
        _wasde_xml(
            "sr16",
            [("Total Supply", "14268"), ("Total Use", "12571"), ("Ending Stocks", "1697"), ("Stocks to Use Ratio", "13.5")],
        ),
    )

    record = EsmisClient().get_report("wasde", "sugar", as_of=datetime(2026, 9, 20, 16, 0, tzinfo=ET))

    assert record == {
        "release_date": "2026-09-11",
        "marketing_year": "2026/27 Proj.",
        "ending_stocks": 1697.0,
        "stocks_to_use": 13.5,
    }


_JUN_2025_LINES = [
    "Coffee Summary, Continued",
    "2020/21 2021/22 2022/23 2023/24 2024/25",
    "Jun",
    "2025/26",
    "Domestic Consumption none",
    "25,922   United States 26,708 24,623 23,550 25,350 25,550",
    "162,093       Total 167,868 168,789 163,921 166,515 169,363",
    "Ending Stocks none",
    "6,023   United States 6,378 5,700 5,700 5,700 5,700",
    "37,494       Total 31,940 26,934 23,121 21,752 22,819",
]


def _balance_lines(month: str, marketing_year: str, use: str, ending: str) -> list[str]:
    return [
        "Coffee Summary, Continued",
        "2020/21 2021/22 2022/23 2023/24 2024/25",
        month,
        marketing_year,
        "Domestic Consumption none",
        f"162,093       Total 167,868 168,789 163,921 166,515 {use}",
        "Ending Stocks none",
        f"37,494       Total 31,940 26,934 23,121 21,752 {ending}",
    ]


def _pdf(lines: list[str], created: str) -> bytes:
    """A minimal single-page PDF whose extractable text is `lines`, one per line, created on ISO date `created`."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 10 Tf 12 TL 40 760 Td " + " ".join(f"({line}) Tj T*" for line in escaped) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /CreationDate (D:{created.replace('-', '')}100000) >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def _serve_coffee(monkeypatch, *editions: tuple[str, list[str]]):
    """Serve FAS circulars the way the real folder does: .../circulars/YYYY/MM/coffee.pdf returns the latest
    edition released in or before that month."""
    pdfs = {created: _pdf(lines, created) for created, lines in editions}

    def fake_get(url, **kwargs):
        match = re.search(r"/circulars/(\d{4})/(\d{2})/coffee\.pdf$", url)
        assert match, url
        month = f"{match[1]}-{match[2]}"
        served = [created for created in pdfs if created[:7] <= month]
        assert served, f"no edition at or before {month}"
        return _Response(content=pdfs[max(served)])

    monkeypatch.setattr(usda.requests, "get", fake_get)


def test_esmis_client_parses_coffee_world_balance_from_jun_2025_report(monkeypatch):
    # Figures from the real Jun 2025 FAS "Coffee: World Markets and Trade" summary table
    # (thousand 60-kg bags): world total consumption 169,363, world total ending stocks 22,819.
    _serve_coffee(monkeypatch, ("2025-06-25", _JUN_2025_LINES))

    record = EsmisClient().get_report("coffee_world_markets", "coffee", as_of=datetime(2025, 7, 15, 16, 0, tzinfo=ET))

    assert record == {
        "release_date": "2025-06-25",
        "marketing_year": "2025/26",
        "ending_stocks": 22819.0,
        "stocks_to_use": 13.47,
    }


def test_esmis_client_ignores_ending_stocks_headline_before_the_real_tables(monkeypatch):
    # Layout of the real Dec 2024 edition: a page-1 headline containing "Ending Stocks" and an Arabica
    # production "Total" row (97,845) both come before the balance tables. World total ending stocks
    # are 20,867 against total consumption 168,071.
    _serve_coffee(
        monkeypatch,
        (
            "2024-12-18",
            [
                "Production in 2025/26 is forecast higher",
                "World Ending Stocks Continue to Tighten",
                "Arabica Production",
                "102,120       Total 87,100 87,779 96,379 99,855 97,845",
                *_balance_lines("Dec", "2025/26", "168,071", "20,867"),
            ],
        ),
    )

    record = EsmisClient().get_report("coffee_world_markets", "coffee", as_of=datetime(2025, 1, 15, 16, 0, tzinfo=ET))

    assert record == {
        "release_date": "2024-12-18",
        "marketing_year": "2025/26",
        "ending_stocks": 20867.0,
        "stocks_to_use": 12.42,
    }


def test_esmis_client_reads_the_newest_coffee_edition_beyond_the_esmis_archive(monkeypatch):
    # The ESMIS archive stops at Jun 2025; the Jul 22 2026 edition is only on the FAS circulars path.
    _serve_coffee(
        monkeypatch,
        ("2025-06-25", _JUN_2025_LINES),
        ("2026-07-22", _balance_lines("Jul", "2026/27", "170,000", "21,000")),
    )

    record = EsmisClient().get_report("coffee_world_markets", "coffee", as_of=datetime(2026, 10, 30, 10, 0, tzinfo=ET))

    assert record["release_date"] == "2026-07-22"
    assert record["ending_stocks"] == 21000.0


def test_esmis_client_skips_a_coffee_edition_released_after_as_of(monkeypatch):
    # The 2026/07 folder already serves the Jul 22 edition on Jul 10, but it was not public yet: the
    # Dec 18 2025 edition is the latest known at that instant.
    _serve_coffee(
        monkeypatch,
        ("2025-12-18", _balance_lines("Dec", "2025/26", "169,000", "20,148")),
        ("2026-07-22", _balance_lines("Jul", "2026/27", "170,000", "21,000")),
    )

    record = EsmisClient().get_report("coffee_world_markets", "coffee", as_of=datetime(2026, 7, 10, 10, 0, tzinfo=ET))

    assert record["release_date"] == "2025-12-18"
    assert record["ending_stocks"] == 20148.0

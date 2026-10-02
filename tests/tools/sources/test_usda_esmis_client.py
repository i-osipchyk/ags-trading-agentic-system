from datetime import date

from ags.tools.sources import usda
from ags.tools.sources.usda import EsmisClient

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

    record = EsmisClient().get_report("wasde", "cotton", as_of=date(2026, 9, 20))

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

    record = EsmisClient().get_report("wasde", "sugar", as_of=date(2026, 9, 20))

    assert record == {
        "release_date": "2026-09-11",
        "marketing_year": "2026/27 Proj.",
        "ending_stocks": 1697.0,
        "stocks_to_use": 13.5,
    }


_COFFEE_LISTING_HTML = (
    '<td><time datetime="2025-06-25T12:00:00Z">Jun 25 2025</time></td>'
    '<td><a href="/sites/default/release-files/m900nt40f/k069b582p/8049j506c/coffee.pdf" class="tag">'
    '<span class="usa-sr-only"><time datetime="2025-06-25T12:00:00Z">Jun 25 2025</time> - </span>pdf</a></td>'
)


def _pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF whose extractable text is `lines`, one per line."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 10 Tf 12 TL 40 760 Td " + " ".join(f"({line}) Tj T*" for line in escaped) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def _serve_coffee(monkeypatch, pdf: bytes):
    def fake_get(url, **kwargs):
        if url.endswith(".pdf"):
            return _Response(content=pdf)
        return _Response(text=_COFFEE_LISTING_HTML)

    monkeypatch.setattr(usda.requests, "get", fake_get)


def test_esmis_client_parses_coffee_world_balance_from_jun_2025_report(monkeypatch):
    # Figures from the real Jun 2025 FAS "Coffee: World Markets and Trade" summary table
    # (thousand 60-kg bags): world total consumption 169,363, world total ending stocks 22,819.
    _serve_coffee(
        monkeypatch,
        _pdf(
            [
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
        ),
    )

    record = EsmisClient().get_report("coffee_world_markets", "coffee", as_of=date(2025, 7, 15))

    assert record == {
        "release_date": "2025-06-25",
        "marketing_year": "2025/26",
        "ending_stocks": 22819.0,
        "stocks_to_use": 13.47,
    }

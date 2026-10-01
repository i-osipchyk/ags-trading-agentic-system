import io
import zipfile
from datetime import date, timedelta
from typing import Protocol

import requests
import shapefile

from ags.tools.pit_store import read_pit, write_pit

SOURCE_DROUGHT = "usdm"
REPORT_DROUGHT = "drought_severity"
SOURCE_OUTLOOK = "noaa_cpc"
REPORT_OUTLOOK = "monthly_outlook"

_USDM_URL = "https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByArea"
_CPC_BASE_URL = "https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/monthlyupdate"

# US ag-belt states covered this stage (corn/soy/wheat/cotton/sugar-beet
# growing regions) — FIPS code for the Drought Monitor API, approximate
# geographic centroid (lon, lat) for the CPC outlook's point-in-polygon
# lookup. Extended lazily as more commodities/regions come into scope
# (same posture as ARCHITECTURE.md's per-commodity source rollout).
_STATES = {
    "IA": {"fips": "19", "lon": -93.5, "lat": 42.0},
    "IL": {"fips": "17", "lon": -89.2, "lat": 40.0},
    "IN": {"fips": "18", "lon": -86.3, "lat": 39.9},
    "NE": {"fips": "31", "lon": -99.8, "lat": 41.5},
    "MN": {"fips": "27", "lon": -94.3, "lat": 46.3},
    "OH": {"fips": "39", "lon": -82.8, "lat": 40.3},
    "SD": {"fips": "46", "lon": -100.3, "lat": 44.5},
    "ND": {"fips": "38", "lon": -100.5, "lat": 47.5},
    "KS": {"fips": "20", "lon": -98.4, "lat": 38.5},
    "MO": {"fips": "29", "lon": -92.5, "lat": 38.5},
    "WI": {"fips": "55", "lon": -89.9, "lat": 44.6},
    "MI": {"fips": "26", "lon": -85.4, "lat": 44.3},
    "TX": {"fips": "48", "lon": -99.3, "lat": 31.5},
    "GA": {"fips": "13", "lon": -83.4, "lat": 32.6},
    "MS": {"fips": "28", "lon": -89.7, "lat": 32.7},
    "AR": {"fips": "05", "lon": -92.4, "lat": 34.9},
    "LA": {"fips": "22", "lon": -91.9, "lat": 31.0},
    "NC": {"fips": "37", "lon": -79.4, "lat": 35.6},
    "TN": {"fips": "47", "lon": -86.4, "lat": 35.9},
    "AL": {"fips": "01", "lon": -86.8, "lat": 32.8},
    "SC": {"fips": "45", "lon": -80.9, "lat": 33.9},
}


SUPPORTED_REGIONS = tuple(sorted(_STATES))


class WeatherClient(Protocol):
    def get_drought_conditions(self, region: str, *, as_of: date) -> dict | None: ...
    def get_outlook(self, region: str, *, as_of: date) -> dict | None: ...


class DroughtMonitorClient:
    """US Drought Monitor weekly severity statistics (usdmdataservices.unl.edu)."""

    def get_drought_conditions(self, region: str, *, as_of: date) -> dict | None:
        fips = _STATES[region]["fips"]
        start = as_of - timedelta(days=60)
        response = requests.get(
            _USDM_URL,
            params={
                "aoi": fips,
                "startdate": f"{start.month}/{start.day}/{start.year}",
                "enddate": f"{as_of.month}/{as_of.day}/{as_of.year}",
                "statisticsType": 1,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json()
        if not records:
            return None

        latest = max(records, key=lambda r: r["mapDate"])
        return {
            "map_date": latest["mapDate"][:10],
            "none_sq_mi": latest["none"],
            "d0_sq_mi": latest["d0"],
            "d1_sq_mi": latest["d1"],
            "d2_sq_mi": latest["d2"],
            "d3_sq_mi": latest["d3"],
            "d4_sq_mi": latest["d4"],
        }


def _prev_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_shape(x: float, y: float, shape) -> bool:
    # Treats every ring as solid (no hole handling) — CPC's outlook zones
    # are large, simply-connected regions, so this is a safe approximation
    # for a single representative point per state.
    if not shape.points:
        return False
    parts = list(shape.parts) + [len(shape.points)]
    return any(_point_in_ring(x, y, shape.points[parts[i] : parts[i + 1]]) for i in range(len(parts) - 1))


def _fetch_outlook_shapefile(product: str, as_of: date):
    # product: "temp" or "prcp". Tries the current month's issuance first,
    # then last month's — the file naming only tells us when a product was
    # issued, not the exact day, so falling back to the prior month is the
    # only way to guarantee a release that's actually <= as_of (PIT safety).
    for month_date in (date(as_of.year, as_of.month, 1), _prev_month(as_of)):
        url = f"{_CPC_BASE_URL}/monthupd_{product}_{month_date.year:04d}{month_date.month:02d}.zip"
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            continue

        zf = zipfile.ZipFile(io.BytesIO(response.content))
        shp_name = next(name for name in zf.namelist() if name.endswith(".shp"))
        base = shp_name[: -len(".shp")]
        reader = shapefile.Reader(
            shp=io.BytesIO(zf.read(base + ".shp")),
            dbf=io.BytesIO(zf.read(base + ".dbf")),
        )
        records = [r.as_dict() for r in reader.records()]
        if not records:
            continue
        fcst_date = records[0]["Fcst_Date"]
        if fcst_date > as_of:
            continue

        return records, reader.shapes(), fcst_date, records[0]["Valid_Seas"]
    return None


def _lookup_category(records: list[dict], shapes: list, lon: float, lat: float) -> dict | None:
    for record, shape in zip(records, shapes):
        if _point_in_shape(lon, lat, shape):
            return {"category": record["Cat"], "probability": record["Prob"]}
    return None


class CpcOutlookClient:
    """NOAA CPC monthly temperature/precipitation outlook (ftp.cpc.ncep.noaa.gov)."""

    def get_outlook(self, region: str, *, as_of: date) -> dict | None:
        state = _STATES[region]

        temp = _fetch_outlook_shapefile("temp", as_of)
        precip = _fetch_outlook_shapefile("prcp", as_of)
        if temp is None or precip is None:
            return None

        temp_records, temp_shapes, temp_fcst_date, valid_period = temp
        precip_records, precip_shapes, precip_fcst_date, _ = precip

        temp_cat = _lookup_category(temp_records, temp_shapes, state["lon"], state["lat"])
        precip_cat = _lookup_category(precip_records, precip_shapes, state["lon"], state["lat"])
        if temp_cat is None or precip_cat is None:
            return None

        return {
            "issued": min(temp_fcst_date, precip_fcst_date).isoformat(),
            "valid_period": valid_period,
            "temp": temp_cat,
            "precip": precip_cat,
        }


class USWeatherClient:
    """Combines DroughtMonitorClient + CpcOutlookClient behind the WeatherClient protocol."""

    def __init__(self):
        self._drought = DroughtMonitorClient()
        self._outlook = CpcOutlookClient()

    def get_drought_conditions(self, region: str, *, as_of: date) -> dict | None:
        return self._drought.get_drought_conditions(region, as_of=as_of)

    def get_outlook(self, region: str, *, as_of: date) -> dict | None:
        return self._outlook.get_outlook(region, as_of=as_of)


def _get_drought(data_dir, *, region: str, as_of: date, client: WeatherClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE_DROUGHT, report=REPORT_DROUGHT, symbol=region, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_drought_conditions(region, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    if date.fromisoformat(record["map_date"]) > as_of:
        return None

    write_pit(
        data_dir,
        source=SOURCE_DROUGHT,
        report=REPORT_DROUGHT,
        symbol=region,
        release_date=date.fromisoformat(record["map_date"]),
        content=record,
    )
    return record


def _get_outlook(data_dir, *, region: str, as_of: date, client: WeatherClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE_OUTLOOK, report=REPORT_OUTLOOK, symbol=region, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_outlook(region, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    if date.fromisoformat(record["issued"]) > as_of:
        return None

    write_pit(
        data_dir,
        source=SOURCE_OUTLOOK,
        report=REPORT_OUTLOOK,
        symbol=region,
        release_date=date.fromisoformat(record["issued"]),
        content=record,
    )
    return record


def get_weather(data_dir, *, region: str, as_of: date, client: WeatherClient | None = None) -> dict:
    if client is None:
        client = USWeatherClient()
    drought = _get_drought(data_dir, region=region, as_of=as_of, client=client)
    outlook = _get_outlook(data_dir, region=region, as_of=as_of, client=client)
    return {"region": region, "drought": drought, "outlook": outlook}

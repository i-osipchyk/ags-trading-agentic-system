import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import requests
import shapefile

from ags.tools.pit_store import ET, is_released, read_pit, write_pit

SOURCE_DROUGHT = "usdm"
REPORT_DROUGHT = "drought_severity"
SOURCE_OUTLOOK = "noaa_cpc"
REPORT_OUTLOOK = "monthly_outlook"
SOURCE_FORECAST = "open_meteo"
REPORT_FORECAST = "forecast"

_USDM_URL = "https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByArea"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
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


# Non-US growing regions, queried through Open-Meteo by representative point (lat, lon).
_OPEN_METEO_POINTS = {
    "BR-MG": {"lat": -21.2, "lon": -45.0},  # Minas Gerais, Brazil's main arabica state
    "BR-SP": {"lat": -20.5, "lon": -47.5},  # Sao Paulo (Alta Mogiana)
    "BR-ES": {"lat": -19.5, "lon": -40.7},  # Espirito Santo (conilon robusta)
    "VN-DL": {"lat": 12.67, "lon": 108.04},  # Dak Lak, Vietnam's robusta heartland
    "CO-HU": {"lat": 2.5, "lon": -75.5},  # Huila, Colombia
    "BR-CS": {"lat": -21.2, "lon": -47.8},  # Center-South cane belt (Ribeirao Preto)
    "IN-MH": {"lat": 19.0, "lon": 75.0},  # Maharashtra, India's top cane state
    "IN-KA": {"lat": 15.3, "lon": 75.7},  # Karnataka
    "TH": {"lat": 15.0, "lon": 100.0},  # Thailand's central cane belt
    "CI": {"lat": 6.8, "lon": -5.3},  # Ivory Coast cocoa belt
    "GH": {"lat": 6.7, "lon": -1.6},  # Ghana cocoa belt (Ashanti)
    "BR-MT": {"lat": -13.0, "lon": -56.0},  # Mato Grosso cotton
    "IN-GJ": {"lat": 22.3, "lon": 71.8},  # Gujarat cotton
    "CN-XJ": {"lat": 41.5, "lon": 85.0},  # Xinjiang cotton
}

# Regions each commodity's analyst may query. A commodity not listed here is a US row crop and gets every
# supported US state.
_REGIONS_BY_COMMODITY = {
    "coffee": ("BR-ES", "BR-MG", "BR-SP", "CO-HU", "VN-DL"),
    "sugar": ("BR-CS", "IN-KA", "IN-MH", "TH"),
    "cocoa": ("CI", "GH"),
    "cotton": ("AL", "AR", "BR-MT", "CN-XJ", "GA", "IN-GJ", "LA", "MO", "MS", "NC", "SC", "TN", "TX"),
}

SUPPORTED_REGIONS = tuple(sorted(_STATES))


def regions_for(commodity: str) -> tuple[str, ...]:
    return _REGIONS_BY_COMMODITY.get(commodity, SUPPORTED_REGIONS)


class WeatherClient(Protocol):
    def get_drought_conditions(self, region: str, *, as_of: datetime) -> dict | None: ...
    def get_outlook(self, region: str, *, as_of: datetime) -> dict | None: ...
    def get_forecast(self, region: str, *, as_of: datetime) -> dict | None: ...


class DroughtMonitorClient:
    """US Drought Monitor weekly severity statistics (usdmdataservices.unl.edu)."""

    def get_drought_conditions(self, region: str, *, as_of: datetime) -> dict | None:
        fips = _STATES[region]["fips"]
        day = as_of.astimezone(ET).date()
        start = day - timedelta(days=60)
        response = requests.get(
            _USDM_URL,
            params={
                "aoi": fips,
                "startdate": f"{start.month}/{start.day}/{start.year}",
                "enddate": f"{day.month}/{day.day}/{day.year}",
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


def _fetch_outlook_shapefile(product: str, as_of: datetime):
    # product: "temp" or "prcp". Tries the current month's issuance first,
    # then last month's — the file naming only tells us when a product was
    # issued, not the exact day, so falling back to the prior month is the
    # only way to guarantee a release that's actually <= as_of (PIT safety).
    day = as_of.astimezone(ET).date()
    for month_date in (date(day.year, day.month, 1), _prev_month(day)):
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
        if not is_released(SOURCE_OUTLOOK, fcst_date, as_of):
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

    def get_outlook(self, region: str, *, as_of: datetime) -> dict | None:
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


def _summarise(days: list[tuple[float, float, float]]) -> dict:
    return {
        "precip_mm": round(sum(precip for precip, _, _ in days), 1),
        "tmin_c": min(tmin for _, tmin, _ in days),
        "tmax_c": max(tmax for _, _, tmax in days),
    }


class OpenMeteoClient:
    """Open-Meteo forecast API (api.open-meteo.com): the past 14 days and the next 7 for a point."""

    def get_forecast(self, region: str, *, as_of: datetime) -> dict | None:
        point = _OPEN_METEO_POINTS[region]
        today = as_of.astimezone(timezone.utc).date()
        response = requests.get(
            _OPEN_METEO_URL,
            params={
                "latitude": point["lat"],
                "longitude": point["lon"],
                "daily": "precipitation_sum,temperature_2m_min,temperature_2m_max",
                "past_days": 14,
                "forecast_days": 7,
                "timezone": "UTC",
            },
            timeout=30,
        )
        response.raise_for_status()
        daily = response.json()["daily"]
        rows = list(
            zip(
                (date.fromisoformat(day) for day in daily["time"]),
                zip(daily["precipitation_sum"], daily["temperature_2m_min"], daily["temperature_2m_max"]),
            )
        )
        return {
            "issued": today.isoformat(),
            "past_14d": _summarise([values for day, values in rows if day < today]),
            "next_7d": _summarise([values for day, values in rows if day >= today]),
        }


class USWeatherClient:
    """Combines DroughtMonitorClient + CpcOutlookClient (US) and OpenMeteoClient (elsewhere) behind the
    WeatherClient protocol."""

    def __init__(self):
        self._drought = DroughtMonitorClient()
        self._outlook = CpcOutlookClient()
        self._forecast = OpenMeteoClient()

    def get_forecast(self, region: str, *, as_of: datetime) -> dict | None:
        return self._forecast.get_forecast(region, as_of=as_of)

    def get_drought_conditions(self, region: str, *, as_of: datetime) -> dict | None:
        return self._drought.get_drought_conditions(region, as_of=as_of)

    def get_outlook(self, region: str, *, as_of: datetime) -> dict | None:
        return self._outlook.get_outlook(region, as_of=as_of)


def _get_drought(data_dir, *, region: str, as_of: datetime, client: WeatherClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE_DROUGHT, report=REPORT_DROUGHT, symbol=region, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_drought_conditions(region, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    if not is_released(SOURCE_DROUGHT, date.fromisoformat(record["map_date"]), as_of):
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


def _get_outlook(data_dir, *, region: str, as_of: datetime, client: WeatherClient) -> dict | None:
    cached = read_pit(data_dir, source=SOURCE_OUTLOOK, report=REPORT_OUTLOOK, symbol=region, as_of=as_of)
    if cached is not None:
        return cached

    record = client.get_outlook(region, as_of=as_of)
    if record is None:
        return None
    # Defensive PIT filter: the client's own date-scoping is a request, not
    # a guarantee, per CLAUDE.md's PIT hard constraint.
    if not is_released(SOURCE_OUTLOOK, date.fromisoformat(record["issued"]), as_of):
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


def _get_forecast(data_dir, *, region: str, as_of: datetime, client: WeatherClient) -> dict | None:
    # A forecast goes stale within hours, so unlike the weekly drought map it is fetched every cycle (never
    # served from the PIT store); each pull is archived under the cycle's own timestamp.
    record = client.get_forecast(region, as_of=as_of)
    if record is not None:
        write_pit(
            data_dir, source=SOURCE_FORECAST, report=REPORT_FORECAST, symbol=region, release_date=as_of, content=record
        )
    return record


def get_weather(data_dir, *, region: str, as_of: datetime, client: WeatherClient | None = None) -> dict:
    if client is None:
        client = USWeatherClient()
    if region in _OPEN_METEO_POINTS:
        return {"region": region, "forecast": _get_forecast(data_dir, region=region, as_of=as_of, client=client)}
    drought = _get_drought(data_dir, region=region, as_of=as_of, client=client)
    outlook = _get_outlook(data_dir, region=region, as_of=as_of, client=client)
    return {"region": region, "drought": drought, "outlook": outlook}

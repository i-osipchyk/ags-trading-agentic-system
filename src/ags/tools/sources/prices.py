from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import crochet
from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAErrorRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from ags.config import Config
from ags.tools.pit_store import read_pit, write_pit

SOURCE = "ctrader"
REPORT = "daily_bars"

_PRICE_SCALE = 100_000

CTRADER_SYMBOL_NAMES = {
    "corn": "Corn",
    "soybeans": "Soybeans",
    "wheat": "Wheat",
    "sugar": "Sugar",
    "coffee": "Coffee",
    "cotton": "Cotton",
    "cocoa": "Cocoa",
}


class PricesClient(Protocol):
    def get_daily_bars(self, symbol_name: str, start: date, end: date) -> list[dict]: ...


class CTraderApiError(Exception):
    pass


def _to_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) * 1000


def _parse_bar(bar) -> dict:
    bar_date = datetime.fromtimestamp(bar.utcTimestampInMinutes * 60, tz=timezone.utc).date()
    low = bar.low / _PRICE_SCALE
    return {
        "date": bar_date.isoformat(),
        "open": low + bar.deltaOpen / _PRICE_SCALE,
        "high": low + bar.deltaHigh / _PRICE_SCALE,
        "low": low,
        "close": low + bar.deltaClose / _PRICE_SCALE,
        "volume": bar.volume,
    }


class CTraderPricesClient:
    """Synchronous facade over the Twisted/crochet cTrader Open API client.

    Requests are sequential by design (one weekly batch run, not a hot
    path), so a single pending Deferred is enough to bridge the
    connect -> app-auth -> account-auth handshake and each subsequent
    request/response pair dispatched through the one message callback the
    underlying SDK exposes.
    """

    def __init__(self, config: Config, *, live: bool = True, timeout: int = 30):
        crochet.setup()
        self._config = config
        self._timeout = timeout
        self._symbol_ids: dict[str, int] = {}
        host = EndPoints.PROTOBUF_LIVE_HOST if live else EndPoints.PROTOBUF_DEMO_HOST
        self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setMessageReceivedCallback(self._on_message)
        self._pending: Deferred | None = None
        self._authorize()

    def _on_connected(self, client):
        request = ProtoOAApplicationAuthReq()
        request.clientId = self._config.ctrader_client_id
        request.clientSecret = self._config.ctrader_client_secret
        client.send(request)

    def _on_message(self, client, message):
        if message.payloadType == ProtoOAErrorRes().payloadType:
            error = Protobuf.extract(message)
            self._fail(CTraderApiError(f"{error.errorCode}: {error.description}"))
            return

        if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            request = ProtoOAAccountAuthReq()
            request.ctidTraderAccountId = self._config.ctrader_account_id
            request.accessToken = self._config.ctrader_access_token
            client.send(request)
            return

        if message.payloadType == ProtoOAAccountAuthRes().payloadType:
            self._resolve(None)
            return

        if message.payloadType == ProtoOASymbolsListRes().payloadType:
            self._resolve(Protobuf.extract(message))
            return

        if message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
            self._resolve(Protobuf.extract(message))
            return

    def _resolve(self, value):
        if self._pending is not None and not self._pending.called:
            self._pending.callback(value)

    def _fail(self, error: Exception):
        if self._pending is not None and not self._pending.called:
            self._pending.errback(Failure(error))

    def _authorize(self) -> None:
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            self._client.startService()
            return self._pending

        _do()

    def _symbol_id(self, symbol_name: str) -> int:
        if not self._symbol_ids:
            @crochet.wait_for(timeout=self._timeout)
            def _do():
                self._pending = Deferred()
                request = ProtoOASymbolsListReq()
                request.ctidTraderAccountId = self._config.ctrader_account_id
                self._client.send(request)
                return self._pending

            res = _do()
            self._symbol_ids = {s.symbolName: s.symbolId for s in res.symbol}

        if symbol_name not in self._symbol_ids:
            raise CTraderApiError(f"Unknown cTrader symbol: {symbol_name}")
        return self._symbol_ids[symbol_name]

    def get_daily_bars(self, symbol_name: str, start: date, end: date) -> list[dict]:
        symbol_id = self._symbol_id(symbol_name)

        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            request = ProtoOAGetTrendbarsReq()
            request.ctidTraderAccountId = self._config.ctrader_account_id
            request.period = ProtoOATrendbarPeriod.D1
            request.symbolId = symbol_id
            request.fromTimestamp = _to_ms(start)
            request.toTimestamp = _to_ms(end) + 24 * 60 * 60 * 1000
            self._client.send(request)
            return self._pending

        res = _do()
        return [_parse_bar(bar) for bar in res.trendbar]


def get_prices(
    data_dir: Path,
    *,
    commodity: str,
    start: date,
    end: date,
    as_of: date,
    client: PricesClient | None = None,
) -> list[dict]:
    effective_end = min(end, as_of)

    bars_by_date: dict[date, dict] = {}
    missing_days: list[date] = []
    day = start
    while day <= effective_end:
        release = read_pit(data_dir, source=SOURCE, report=REPORT, symbol=commodity, as_of=day)
        if release is not None and release["date"] == day.isoformat():
            if not release.get("no_trading"):
                bars_by_date[day] = release
        else:
            missing_days.append(day)
        day += timedelta(days=1)

    if missing_days:
        if client is None:
            client = CTraderPricesClient(Config.from_env())
        symbol_name = CTRADER_SYMBOL_NAMES[commodity]
        fetched_bars = client.get_daily_bars(symbol_name, missing_days[0], missing_days[-1])
        fetched_by_date = {date.fromisoformat(bar["date"]): bar for bar in fetched_bars}

        for missing_day in missing_days:
            # No bar came back for this release_date (e.g. a weekend or
            # exchange holiday) — cache that fact too, immutably, so the
            # gap doesn't get re-requested on every future call.
            bar = fetched_by_date.get(missing_day, {"date": missing_day.isoformat(), "no_trading": True})
            write_pit(data_dir, source=SOURCE, report=REPORT, symbol=commodity, release_date=missing_day, content=bar)
            if not bar.get("no_trading") and start <= missing_day <= effective_end:
                bars_by_date[missing_day] = bar

    return [bars_by_date[d] for d in sorted(bars_by_date)]

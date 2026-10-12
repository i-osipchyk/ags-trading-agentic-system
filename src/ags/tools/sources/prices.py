import json
from datetime import date, datetime, time, timedelta, timezone
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
    ProtoOAGetDynamicLeverageByIDReq,
    ProtoOAGetDynamicLeverageByIDRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
    ProtoOAUnsubscribeSpotsReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOADayOfWeek,
    ProtoOASwapCalculationType,
    ProtoOATrendbarPeriod,
)
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from ags.config import Config
from ags.storage import as_storage
from ags.tools.pit_store import ET, end_of_day, read_pit, write_pit

SOURCE = "ctrader"
REPORT = "daily_bars"
SESSION_CLOSE_ET = time(17, 0)

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


def parse_symbol_specs(symbol, leverage) -> dict:
    """Flatten a ProtoOASymbol (+ its ProtoOADynamicLeverage) to plain values.

    cTrader sends leverage in hundredths (2000 = 20:1); margin_rate is the
    reciprocal of the first tier's leverage.
    """
    tiers = [{"volume": t.volume, "leverage": t.leverage / 100} for t in leverage.tiers]
    return {
        "symbol_id": symbol.symbolId,
        "lot_size": symbol.lotSize,
        "min_volume": symbol.minVolume,
        "step_volume": symbol.stepVolume,
        "max_volume": symbol.maxVolume,
        "swap_long": symbol.swapLong,
        "swap_short": symbol.swapShort,
        "swap_type": ProtoOASwapCalculationType.Name(symbol.swapCalculationType),
        "triple_swap_day": ProtoOADayOfWeek.Name(symbol.swapRollover3Days),
        "digits": symbol.digits,
        "pip_position": symbol.pipPosition,
        "margin_rate": 1 / tiers[0]["leverage"],
        "leverage_tiers": tiers,
    }


def parse_h1_bar(bar) -> dict:
    low = bar.low
    return {
        "timestamp": datetime.fromtimestamp(bar.utcTimestampInMinutes * 60, tz=timezone.utc).isoformat(),
        "open": round((low + bar.deltaOpen) / _PRICE_SCALE, 5),
        "high": round((low + bar.deltaHigh) / _PRICE_SCALE, 5),
        "low": round(low / _PRICE_SCALE, 5),
        "close": round((low + bar.deltaClose) / _PRICE_SCALE, 5),
        "volume": bar.volume,
    }


def parse_quote(event) -> dict:
    return {
        "symbol_id": event.symbolId,
        "bid": event.bid / _PRICE_SCALE,
        "ask": event.ask / _PRICE_SCALE,
        "timestamp": datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc).isoformat(),
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

        if message.payloadType in (
            ProtoOAGetTrendbarsRes().payloadType,
            ProtoOASymbolByIdRes().payloadType,
            ProtoOAGetDynamicLeverageByIDRes().payloadType,
            ProtoOASpotEvent().payloadType,
        ):
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

    def _request(self, request):
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            self._client.send(request)
            return self._pending

        return _do()

    def get_symbol_specs(self, symbol_name: str) -> dict:
        account = self._config.ctrader_account_id
        by_id = ProtoOASymbolByIdReq(ctidTraderAccountId=account)
        by_id.symbolId.append(self._symbol_id(symbol_name))
        symbol = self._request(by_id).symbol[0]
        leverage = self._request(
            ProtoOAGetDynamicLeverageByIDReq(ctidTraderAccountId=account, leverageId=symbol.leverageId)
        ).leverage
        return parse_symbol_specs(symbol, leverage)

    def get_quote(self, symbol_name: str) -> dict:
        """Subscribe, take the first tick carrying both sides, unsubscribe."""
        account = self._config.ctrader_account_id
        symbol_id = self._symbol_id(symbol_name)
        subscribe = ProtoOASubscribeSpotsReq(ctidTraderAccountId=account)
        subscribe.symbolId.append(symbol_id)
        unsubscribe = ProtoOAUnsubscribeSpotsReq(ctidTraderAccountId=account)
        unsubscribe.symbolId.append(symbol_id)
        try:
            event = self._request(subscribe)
            while not (event.bid and event.ask):
                event = self._wait_next()
        finally:
            self._client.send(unsubscribe)
        return parse_quote(event)

    def _wait_next(self):
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            return self._pending

        return _do()

    def get_h1_bars(self, symbol_name: str, start: datetime, end: datetime) -> list[dict]:
        request = ProtoOAGetTrendbarsReq(
            ctidTraderAccountId=self._config.ctrader_account_id,
            period=ProtoOATrendbarPeriod.H1,
            symbolId=self._symbol_id(symbol_name),
            fromTimestamp=int(start.timestamp() * 1000),
            toTimestamp=int(end.timestamp() * 1000),
        )
        return [parse_h1_bar(bar) for bar in self._request(request).trendbar]

    def close(self) -> None:
        """Tear down the connection. Must be called when done with a client
        this object's own code constructed (see get_prices) — crochet gives
        the whole process one shared reactor thread, and an un-closed
        connection left running in the background is what makes a second
        client's own handshake miss its 5s response timeout."""

        @crochet.wait_for(timeout=self._timeout)
        def _do():
            return self._client.stopService()

        _do()


def _session_date(stamp: date) -> date:
    # cTrader stamps a D1 bar with its open time, the evening before the
    # session it covers (Mon's session is stamped Sunday, Fri's Thursday).
    return stamp + timedelta(days=1)


def get_prices(
    data_dir: Path,
    *,
    commodity: str,
    start: date,
    end: date,
    as_of: datetime,
    client: PricesClient | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Daily bars keyed by the session they cover (Mon-Fri), not by cTrader's open-time stamp.

    A session is only public once it has closed (SESSION_CLOSE_ET), so a bar
    is returned only for sessions already closed at `as_of` (and at the real
    now). A 04:00 ET run therefore never sees the in-progress session, and a
    Saturday or Sunday run sees Friday's close as its latest bar. A bar for a
    session still in progress is never read or cached — caching "no bar yet"
    for it would otherwise stick forever (the PIT cache is immutable).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    cutoff = min(as_of, now).astimezone(ET)
    last_session = min(end, (cutoff - timedelta(hours=SESSION_CLOSE_ET.hour, minutes=SESSION_CLOSE_ET.minute)).date())
    # The PIT cache stays keyed by cTrader's own stamp date.
    stamp_start = start - timedelta(days=1)
    stamp_end = last_session - timedelta(days=1)

    bars_by_date: dict[date, dict] = {}
    missing_days: list[date] = []
    day = stamp_start
    while day <= stamp_end:
        release = read_pit(data_dir, source=SOURCE, report=REPORT, symbol=commodity, as_of=end_of_day(day))
        if release is not None and release["date"] == day.isoformat():
            if not release.get("no_trading"):
                bars_by_date[day] = release
        else:
            missing_days.append(day)
        day += timedelta(days=1)

    if missing_days:
        owns_client = client is None
        if client is None:
            client = CTraderPricesClient(Config.from_env())
        try:
            symbol_name = CTRADER_SYMBOL_NAMES[commodity]
            fetched_bars = client.get_daily_bars(symbol_name, missing_days[0], missing_days[-1])
        finally:
            # A client we constructed ourselves is ours to tear down — an
            # injected client is the caller's, who may reuse it for further
            # calls. Left open, it keeps running on crochet's one shared
            # reactor thread and can make a later client's own handshake
            # miss its response timeout (see CTraderPricesClient.close).
            if owns_client:
                client.close()
        fetched_by_date = {date.fromisoformat(bar["date"]): bar for bar in fetched_bars}

        for missing_day in missing_days:
            # No bar came back for this stamp (e.g. a weekend or exchange
            # holiday) — cache that fact too, immutably, so the gap doesn't
            # get re-requested on every future call. Safe because every day
            # here belongs to a closed session (see docstring).
            bar = fetched_by_date.get(missing_day, {"date": missing_day.isoformat(), "no_trading": True})
            write_pit(data_dir, source=SOURCE, report=REPORT, symbol=commodity, release_date=missing_day, content=bar)
            if not bar.get("no_trading"):
                bars_by_date[missing_day] = bar

    return [{**bars_by_date[d], "date": _session_date(d).isoformat()} for d in sorted(bars_by_date)]


SPECS_REPORT = "symbol_specs"


class MarketDataClient(Protocol):
    def get_symbol_specs(self, symbol_name: str) -> dict: ...


def get_symbol_specs(
    data_dir: Path,
    *,
    commodity: str,
    as_of: datetime,
    client: MarketDataClient | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Symbol specs as known at `as_of`; refetched at most once per ET day.

    Stored PIT under the fetch instant (not just the date, which would only
    become visible at end of day) so a later cycle the same day sees it. An
    `as_of` on a past day only reads what was stored — specs can't be fetched
    retroactively.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    cutoff = min(as_of, now)
    known = read_pit(data_dir, source=SOURCE, report=SPECS_REPORT, symbol=commodity, as_of=cutoff)
    today = cutoff.astimezone(ET).date()
    is_live = as_of.astimezone(ET).date() == now.astimezone(ET).date()
    if known is not None and datetime.fromisoformat(known["fetched_at"]).astimezone(ET).date() == today:
        return known["specs"]
    if not is_live:
        return known["specs"] if known is not None else None

    owns_client = client is None
    if client is None:
        client = CTraderPricesClient(Config.from_env())
    try:
        specs = client.get_symbol_specs(CTRADER_SYMBOL_NAMES[commodity])
    finally:
        if owns_client:
            client.close()
    write_pit(
        data_dir, source=SOURCE, report=SPECS_REPORT, symbol=commodity, release_date=cutoff,
        content={"fetched_at": cutoff.isoformat(), "specs": specs},
    )
    return specs


QUOTES_REPORT = "quotes"


def get_quote(
    data_dir: Path,
    *,
    commodity: str,
    as_of: datetime,
    client: MarketDataClient | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Bid/ask snapshot as of `as_of`, stored PIT under its capture instant.

    A call at the present (`as_of >= now`) captures a fresh quote; any earlier
    `as_of` only reads the latest quote already captured by then.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    if as_of < now:
        return read_pit(data_dir, source=SOURCE, report=QUOTES_REPORT, symbol=commodity, as_of=as_of)

    owns_client = client is None
    if client is None:
        client = CTraderPricesClient(Config.from_env())
    try:
        quote = client.get_quote(CTRADER_SYMBOL_NAMES[commodity])
    finally:
        if owns_client:
            client.close()
    write_pit(data_dir, source=SOURCE, report=QUOTES_REPORT, symbol=commodity, release_date=now, content=quote)
    return quote


H1_REPORT = "h1_bars"


def get_h1_bars(
    data_dir: Path,
    *,
    commodity: str,
    start: datetime,
    end: datetime,
    as_of: datetime,
    client: MarketDataClient | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Completed H1 bars with open time in [start, end), as known at `as_of`.

    Each live fetch is stored PIT under its capture instant, along with how far
    it covers; a call only fetches the hours after what earlier captures
    already cover. A bar is returned only once its hour has closed, and only
    from captures made at or before `as_of`.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now or datetime.now(timezone.utc)
    cutoff = min(as_of, now)
    horizon = cutoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

    store = as_storage(data_dir)
    prefix = f"{SOURCE}/{H1_REPORT}/{commodity}/"
    captures = [json.loads(store.get(key)) for key in store.list(prefix) if key.endswith(".json")]
    captures = [c for c in captures if datetime.fromisoformat(c["captured_at"]) <= cutoff]
    covered = max((datetime.fromisoformat(c["covers_until"]) for c in captures), default=None)

    if as_of >= now and (covered is None or covered < horizon):
        owns_client = client is None
        if client is None:
            client = CTraderPricesClient(Config.from_env())
        try:
            fetched = client.get_h1_bars(CTRADER_SYMBOL_NAMES[commodity], covered or start, horizon)
        finally:
            if owns_client:
                client.close()
        capture = {"captured_at": now.isoformat(), "covers_until": horizon.isoformat(), "bars": fetched}
        write_pit(data_dir, source=SOURCE, report=H1_REPORT, symbol=commodity, release_date=now, content=capture)
        captures.append(capture)

    by_open = {b["timestamp"]: b for c in captures for b in c["bars"]}
    return [
        b
        for b in sorted(by_open.values(), key=lambda b: b["timestamp"])
        if start <= datetime.fromisoformat(b["timestamp"]) < min(end, horizon)
    ]

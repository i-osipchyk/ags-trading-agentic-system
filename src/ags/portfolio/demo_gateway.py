"""The only module that sends orders: a cTrader Open API connection to the Pepperstone *demo* host.

`tests/test_no_live_execution.py` allows trading messages in this file alone. The gateway refuses to
connect unless the access token can trade and the account is a demo account.
"""

from datetime import UTC, datetime

import crochet
from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAClosePositionReq,
    ProtoOADealListReq,
    ProtoOADealListRes,
    ProtoOAErrorRes,
    ProtoOAExecutionEvent,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOANewOrderReq,
    ProtoOAOrderErrorEvent,
    ProtoOAOrderListReq,
    ProtoOAOrderListRes,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAAccountType,
    ProtoOAClientPermissionScope,
    ProtoOAExecutionType,
    ProtoOAOrderType,
    ProtoOATradeSide,
)
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from ags.config import Config
from ags.portfolio.demo_book import LABEL, TradingError

_FINAL = (ProtoOAExecutionType.ORDER_FILLED, ProtoOAExecutionType.ORDER_REJECTED)
_PAYLOAD = {
    cls().payloadType: cls
    for cls in (
        ProtoOAApplicationAuthRes, ProtoOAAccountAuthRes, ProtoOAGetAccountListByAccessTokenRes,
        ProtoOATraderRes, ProtoOAReconcileRes, ProtoOAOrderListRes, ProtoOADealListRes, ProtoOAExecutionEvent, ProtoOAOrderErrorEvent,
    )
}


class CTraderDemoGateway:
    """Synchronous facade over the Twisted client, like `CTraderPricesClient`, but pinned to the demo host."""

    def __init__(self, config: Config, *, timeout: int = 30, verify: bool = True):
        crochet.setup()
        self._config = config
        self._account = config.ctrader_trading_account_id
        self._timeout = timeout
        self._pending: Deferred | None = None
        self._accept = lambda message: True
        self._client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setMessageReceivedCallback(self._on_message)
        self._start()
        if verify:
            self._require_demo_account_that_can_trade()

    def _on_connected(self, client):
        client.send(ProtoOAApplicationAuthReq(clientId=self._config.ctrader_client_id,
                                              clientSecret=self._config.ctrader_client_secret))

    def _on_message(self, client, message):
        if message.payloadType == ProtoOAErrorRes().payloadType:
            error = Protobuf.extract(message)
            self._settle(Failure(TradingError(f"{error.errorCode}: {error.description}")))
        elif message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            self._settle(None)  # the account is authorized later, once we know it is a demo one
        elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
            self._settle(None)
        elif message.payloadType in _PAYLOAD:
            decoded = Protobuf.extract(message)
            if isinstance(decoded, ProtoOAOrderErrorEvent):
                self._settle(Failure(TradingError(f"{decoded.errorCode}: {decoded.description}")))
            elif self._accept(decoded):
                self._settle(decoded)

    def _settle(self, value):
        if self._pending is None or self._pending.called:
            return
        if isinstance(value, Failure):
            self._pending.errback(value)
        else:
            self._pending.callback(value)

    def _start(self) -> None:
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            self._client.startService()
            return self._pending

        _do()

    def _request(self, request, accept=lambda message: True):
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            self._pending = Deferred()
            self._accept = accept
            self._client.send(request)
            return self._pending

        try:
            return _do()
        except crochet.TimeoutError as exc:
            raise TradingError("no response from cTrader; the order's outcome is unknown") from exc

    def _account_list(self):
        return self._request(
            ProtoOAGetAccountListByAccessTokenReq(accessToken=self._config.ctrader_access_token),
            lambda m: isinstance(m, ProtoOAGetAccountListByAccessTokenRes),
        )

    def accounts(self) -> list[dict]:
        """Every account on the token as `{ctid, login, live}`. `ctid` is the id CTRADER_ACCOUNT_ID takes."""
        return [
            {"ctid": a.ctidTraderAccountId, "login": a.traderLogin, "live": a.isLive}
            for a in self._account_list().ctidTraderAccount
        ]

    def _require_demo_account_that_can_trade(self) -> None:
        res = self._account_list()
        if res.permissionScope != ProtoOAClientPermissionScope.SCOPE_TRADE:
            raise TradingError("the cTrader access token has no trading scope")
        on_token = ", ".join(
            f"{a.ctidTraderAccountId} ({'LIVE' if a.isLive else 'demo'})" for a in res.ctidTraderAccount
        ) or "none"
        account = next((a for a in res.ctidTraderAccount if a.ctidTraderAccountId == self._account), None)
        if account is None:
            raise TradingError(f"account {self._account} is not on this token; the token has: {on_token}")
        if account.isLive:
            raise TradingError(
                f"account {self._account} is a live account; only demo accounts may trade. The token has: {on_token}"
            )
        try:
            self._request(
                ProtoOAAccountAuthReq(ctidTraderAccountId=self._account, accessToken=self._config.ctrader_access_token),
                lambda m: isinstance(m, ProtoOAAccountAuthRes),
            )
        except TradingError as exc:
            if "CANT_ROUTE_REQUEST" in str(exc):
                raise TradingError(
                    f"{exc}: account {self._account} is a demo account but the demo server will not route it. "
                    f"The token has: {on_token}"
                ) from exc
            raise
        trader = self._request(
            ProtoOATraderReq(ctidTraderAccountId=self._account), lambda m: isinstance(m, ProtoOATraderRes)
        ).trader
        if trader.accountType != ProtoOAAccountType.HEDGED:
            # On a netting account other strategies' positions in the same symbol merge with ours.
            raise TradingError(f"account {self._account} is not a hedging account; the manager needs one")

    def account(self) -> dict:
        trader = self._request(
            ProtoOATraderReq(ctidTraderAccountId=self._account), lambda m: isinstance(m, ProtoOATraderRes)
        ).trader
        reconcile = self._request(
            ProtoOAReconcileReq(ctidTraderAccountId=self._account), lambda m: isinstance(m, ProtoOAReconcileRes)
        )
        positions = []
        for p in reconcile.position:
            scale = 10 ** p.moneyDigits
            positions.append({
                "position_id": p.positionId, "symbol_id": p.tradeData.symbolId, "label": p.tradeData.label,
                "trade_side": ProtoOATradeSide.Name(p.tradeData.tradeSide), "volume": p.tradeData.volume,
                "price": p.price, "open_timestamp": datetime.fromtimestamp(p.tradeData.openTimestamp / 1000).isoformat(),
                "swap": p.swap / scale, "commission": p.commission / scale,
            })
        return {"balance": trader.balance / 10 ** trader.moneyDigits, "positions": positions}

    def place_market(self, *, symbol_id: int, side: str, volume: int, client_order_id: str) -> dict:
        request = ProtoOANewOrderReq(
            ctidTraderAccountId=self._account, symbolId=symbol_id, orderType=ProtoOAOrderType.MARKET,
            tradeSide=ProtoOATradeSide.Value(side), volume=volume, clientOrderId=client_order_id,
            label=LABEL, comment=client_order_id,
        )
        return _result(self._request(request, lambda m: _is_final(m) and m.order.clientOrderId == client_order_id))

    def close_position(self, *, position_id: int, volume: int) -> dict:
        request = ProtoOAClosePositionReq(ctidTraderAccountId=self._account, positionId=position_id, volume=volume)
        return _result(self._request(request, lambda m: _is_final(m) and m.order.positionId == position_id))

    def orders_since(self, since: datetime) -> list[dict]:
        res = self._request(
            ProtoOAOrderListReq(
                ctidTraderAccountId=self._account, fromTimestamp=int(since.timestamp() * 1000),
                toTimestamp=int(datetime.now(UTC).timestamp() * 1000) + 60_000,
            ),
            lambda m: isinstance(m, ProtoOAOrderListRes),
        )
        return [
            {"client_order_id": o.clientOrderId, "position_id": o.positionId, "closing": o.closingOrder,
             "is_stop_out": o.isStopOut}
            for o in res.order
        ]

    def deals_since(self, since: datetime) -> list[dict]:
        res = self._request(
            ProtoOADealListReq(
                ctidTraderAccountId=self._account, fromTimestamp=int(since.timestamp() * 1000),
                toTimestamp=int(datetime.now(UTC).timestamp() * 1000) + 60_000, maxRows=1000,
            ),
            lambda m: isinstance(m, ProtoOADealListRes),
        )
        deals = []
        for d in res.deal:
            detail = d.closePositionDetail
            closing = detail.closedVolume > 0
            deals.append({
                "deal_id": d.dealId, "position_id": d.positionId, "closing": closing,
                "commission": d.commission / 10 ** d.moneyDigits,
                "gross_profit": detail.grossProfit / 10 ** detail.moneyDigits if closing else 0.0,
                "swap": detail.swap / 10 ** detail.moneyDigits if closing else 0.0,
            })
        return deals

    def close(self) -> None:
        @crochet.wait_for(timeout=self._timeout)
        def _do():
            return self._client.stopService()

        _do()


def _is_final(message) -> bool:
    return isinstance(message, ProtoOAExecutionEvent) and message.executionType in _FINAL


def _result(event) -> dict:
    if event.executionType == ProtoOAExecutionType.ORDER_REJECTED:
        raise TradingError(f"order rejected: {event.errorCode}")
    deal = event.deal
    scale = 10 ** deal.moneyDigits
    detail = deal.closePositionDetail
    closing = detail.closedVolume > 0
    return {
        "order_id": event.order.orderId, "position_id": deal.positionId, "price": deal.executionPrice,
        "commission": deal.commission / scale,
        "gross_profit": detail.grossProfit / 10 ** detail.moneyDigits if closing else 0.0,
        "swap": detail.swap / 10 ** detail.moneyDigits if closing else 0.0,
    }

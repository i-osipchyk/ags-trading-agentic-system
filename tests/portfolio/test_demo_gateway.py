import pytest
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType

from ags.portfolio.demo_book import TradingError
from ags.portfolio.demo_gateway import _is_final, _result


def _event(kind, **deal):
    event = ProtoOAExecutionEvent(ctidTraderAccountId=1, executionType=kind)
    event.order.orderId = 55
    for key, value in deal.items():
        setattr(event.deal, key, value)
    return event


def test_an_opening_fill_carries_price_and_scaled_commission():
    event = _event(ProtoOAExecutionType.ORDER_FILLED, positionId=9, executionPrice=5.01, commission=-300, moneyDigits=2)

    assert _result(event) == {
        "order_id": 55, "position_id": 9, "price": 5.01, "commission": -3.0, "gross_profit": 0.0, "swap": 0.0,
    }


def test_a_closing_fill_carries_gross_profit_and_swap():
    event = _event(ProtoOAExecutionType.ORDER_FILLED, positionId=9, executionPrice=4.99, commission=-300, moneyDigits=2)
    detail = event.deal.closePositionDetail
    detail.closedVolume, detail.grossProfit, detail.swap, detail.moneyDigits = 20_000, -2000, -50, 2

    assert _result(event)["gross_profit"] == -20.0 and _result(event)["swap"] == -0.5


def test_a_rejection_raises_and_only_final_events_end_the_wait():
    rejected = _event(ProtoOAExecutionType.ORDER_REJECTED)
    assert _is_final(rejected) and not _is_final(_event(ProtoOAExecutionType.ORDER_ACCEPTED))
    with pytest.raises(TradingError):
        _result(rejected)

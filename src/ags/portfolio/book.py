from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Protocol


@dataclass
class Position:
    """Mirrors the fields of cTrader's ProtoOAPosition / ProtoOATradeData the book needs.

    `volume` is in cTrader units (1/100 of a unit); lots = volume / lot_size.
    One net position per symbol.
    """

    position_id: int
    symbol_name: str
    symbol_id: int
    trade_side: str  # "BUY" | "SELL"
    volume: int
    price: float
    open_timestamp: str
    swap: float = 0.0
    commission: float = 0.0


def order_volume(lots: float, spec: dict) -> int:
    """Whole `step_volume`s of `abs(lots)`, rounded toward zero; 0 when below `min_volume`."""
    volume = int(abs(lots) * spec["lot_size"] / spec["step_volume"]) * spec["step_volume"]
    return volume if volume >= spec["min_volume"] else 0


def is_market_open(spec: dict, at: datetime) -> bool:
    """Whether `at` falls in one of the symbol's trading sessions. Unknown schedule counts as open.

    Holidays and early closes are not in the weekly schedule; the broker rejects those orders.
    """
    intervals, tz = spec.get("schedule"), spec.get("schedule_timezone")
    if not intervals or not tz:
        return True
    local = at.astimezone(ZoneInfo(tz))
    # cTrader's week starts Sunday 00:00; Python's Monday is 0.
    seconds = ((local.weekday() + 1) % 7) * 86_400 + local.hour * 3_600 + local.minute * 60 + local.second
    return any(start <= seconds < end for start, end in intervals)


class Book(Protocol):
    """`PaperBook` (simulated) or `DemoBook` (the Pepperstone demo account). No live implementation exists."""

    def apply_fills(self, deltas_lots: dict[str, float], quotes: dict, specs: dict, timestamp: str) -> list[dict]: ...
    def snapshot(self, quotes: dict, specs: dict) -> dict: ...
    def state(self) -> dict: ...

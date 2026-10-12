from dataclasses import dataclass
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


class Book(Protocol):
    """Paper-only today. Deliberately has no order-routing method beyond fills of deltas."""

    def apply_fills(self, deltas_lots: dict[str, float], quotes: dict, specs: dict, timestamp: str) -> list[dict]: ...
    def snapshot(self, quotes: dict, specs: dict) -> dict: ...
    def state(self) -> dict: ...

from datetime import datetime, timezone
from typing import Protocol

from ags.portfolio.book import is_market_open, order_volume
from ags.portfolio.paper_book import PaperBook

# Every order the manager sends carries this label, which is how its positions are told apart from
# other strategies' on the same account.
LABEL = "ags"


class TradingError(Exception):
    """An order the broker rejected, or whose outcome could not be confirmed."""


class TradingGateway(Protocol):
    """The demo account's broker connection. Money values are in account currency, volumes in 1/100 units.

    `account()` returns `{"balance": float, "positions": [...]}` for the *whole* account, each position a
    dict with `position_id`, `symbol_id`, `label`, `trade_side`, `volume`, `price`, `open_timestamp`, `swap`,
    `commission`. `place_market` and `close_position` return `{"order_id", "position_id", "price",
    "commission", "gross_profit", "swap"}` and raise `TradingError`. `orders_since(t)` lists the account's
    orders as `{"client_order_id", "position_id", "closing", "is_stop_out"}`; `deals_since(t)` its deals as
    `{"deal_id", "position_id", "closing", "commission", "gross_profit", "swap"}`.
    """

    def account(self) -> dict: ...
    def place_market(self, *, symbol_id: int, side: str, volume: int, client_order_id: str) -> dict: ...
    def close_position(self, *, position_id: int, volume: int) -> dict: ...
    def orders_since(self, since) -> list[dict]: ...
    def deals_since(self, since) -> list[dict]: ...


class DemoBook:
    """The manager's book on the Pepperstone demo account, which other strategies share.

    Same interface as `PaperBook`, with two differences that come from the shared account:

    * **Equity is a ledger, not the account's.** `balance` starts from the manager's own last cycle
      record (or its starting capital) and only ever changes by what the manager itself trades:
      realized P&L, swap and commission, booked as the broker books them. Equity is that balance plus
      the open P&L and swap of the manager's own positions, so it is `capital +/- the manager's P&L`.
    * **Only labelled positions are the manager's.** Positions with another label are never read,
      closed or counted in margin.

    The broker applies swap, margin and stop-outs, so `replay` does nothing; a close the manager did not
    send (a stop-out, a manual close) is picked up by `reconcile`. A rejected or unconfirmed order is
    recorded as a fill with `status != "filled"` and the cycle carries on. Fee fields on a fill are
    signed as the broker books them (negative = charged).
    """

    def __init__(
        self, gateway: TradingGateway, specs: dict, *, cycle_id: str, balance: float,
        peak_equity: float | None = None, marks: dict | None = None, known_position_ids=(),
        clock=lambda: datetime.now(timezone.utc),
    ):
        self._clock = clock
        self._gateway = gateway
        self._specs = specs
        self._cycle_id = cycle_id
        self._by_id = {spec["symbol_id"]: symbol for symbol, spec in specs.items()}
        self._balance = balance
        self._peak = balance if peak_equity is None else peak_equity
        self._marks = marks
        self._known_ids = set(known_position_ids)
        self._refresh()

    def _refresh(self) -> None:
        """Re-read the manager's own positions from the broker. The balance stays the ledger's."""
        own = [p for p in self._gateway.account()["positions"] if p.get("label") == LABEL]
        self._positions = [p for p in own if p["symbol_id"] in self._by_id]

    def _net(self) -> list[dict]:
        """One position per symbol: the account may hold several same-side positions (hedging account)."""
        net: dict[str, dict] = {}
        for p in self._positions:
            symbol = self._by_id[p["symbol_id"]]
            if symbol not in net:
                net[symbol] = {**p, "symbol_name": symbol, "position_ids": [p["position_id"]]}
                continue
            n = net[symbol]
            total = n["volume"] + p["volume"]
            n["price"] = (n["price"] * n["volume"] + p["price"] * p["volume"]) / total
            n["volume"] = total
            n["swap"] += p["swap"]
            n["commission"] += p["commission"]
            n["position_ids"].append(p["position_id"])
        return list(net.values())

    def state(self) -> dict:
        positions = self._net()
        state = {
            "balance": self._balance, "peak_equity": self._peak, "positions": positions,
            # keeps the state loadable by PaperBook (switching the book back to paper)
            "next_position_id": max((p["position_id"] for p in positions), default=0) + 1,
        }
        if self._marks is not None:
            state["marks"] = dict(self._marks)
        return state

    def snapshot(self, quotes: dict, specs: dict) -> dict:
        snap = PaperBook(self.state()).snapshot(quotes, specs)
        self._peak = snap["peak_equity"]
        return snap

    def rebase_peak(self, equity: float) -> None:
        self._peak = equity

    def mark_to(self, quotes: dict) -> None:
        self._marks = {s: (q["bid"] + q["ask"]) / 2 for s, q in quotes.items()}

    def replay(self, h1_bars: dict, specs: dict, since, until) -> list[dict]:
        return []

    def own_position_ids(self) -> set[int]:
        return self._known_ids | {p["position_id"] for p in self._positions}

    def already_traded(self, since) -> bool:
        """Whether an earlier attempt at this cycle already sent orders (the account's other orders don't count)."""
        own = self.own_position_ids()
        return any(
            o["client_order_id"].startswith(f"{self._cycle_id}:")
            or (o["closing"] and not o["is_stop_out"] and o["position_id"] in own)
            for o in self._gateway.orders_since(since)
        )

    def reconcile(self, since) -> list[dict]:
        """Book the realized result of the manager's positions that closed without the manager (stop-out, manual).

        Those are the positions last cycle's record held that the broker no longer has. Their closing deals
        are read from the account's history; the opening commission was booked when the position opened.
        """
        gone = self._known_ids - {p["position_id"] for p in self._positions}
        if not gone:
            return []
        closes = [d for d in self._gateway.deals_since(since) if d["closing"] and d["position_id"] in gone]
        for d in closes:
            self._balance += d["gross_profit"] + d["swap"] + d["commission"]
        return closes

    def apply_fills(self, deltas_lots: dict[str, float], quotes: dict, specs: dict, timestamp: str) -> list[dict]:
        fills: list[dict] = []
        for symbol, lots in deltas_lots.items():
            volume = order_volume(lots, specs[symbol])
            if not volume:
                continue
            side = "BUY" if lots > 0 else "SELL"
            if not is_market_open(specs[symbol], self._clock()):
                fills.append(_failure(symbol, side, volume, timestamp, "market closed", status="market_closed"))
                continue
            self._trade(symbol, side, volume, specs[symbol], timestamp, fills)
        try:
            self._refresh()
        except Exception as exc:  # the next cycle re-reads the account; this record keeps the pre-trade state
            fills.append(_failure("*", "-", 0, timestamp, f"account refresh failed: {exc}"))
        return fills

    def _book(self, fill: dict) -> dict:
        """Add a fill's result to the ledger: realized P&L and swap on a close, and the deal's commission."""
        self._balance += fill["realized_pnl"] + fill["commission"]
        return fill

    def _trade(self, symbol: str, side: str, volume: int, spec: dict, timestamp: str, fills: list[dict]) -> None:
        held = [p for p in self._positions if p["symbol_id"] == spec["symbol_id"]]
        remaining = volume
        for position in [p for p in held if p["trade_side"] != side]:
            size = min(remaining, position["volume"])
            if not size:
                break
            try:
                result = self._gateway.close_position(position_id=position["position_id"], volume=size)
            except Exception as exc:
                fills.append(_failure(symbol, side, size, timestamp, str(exc)))
                return
            fills.append(self._book(_fill(symbol, side, size, result, timestamp, closing=True)))
            remaining -= size
        if remaining <= 0:
            return
        client_order_id = f"{self._cycle_id}:{symbol}"[:50]
        try:
            result = self._gateway.place_market(
                symbol_id=spec["symbol_id"], side=side, volume=remaining, client_order_id=client_order_id
            )
        except Exception as exc:
            fills.append(_failure(symbol, side, remaining, timestamp, str(exc)))
            return
        fills.append(self._book(_fill(symbol, side, remaining, result, timestamp, closing=False)))


def _fill(symbol: str, side: str, volume: int, result: dict, timestamp: str, *, closing: bool) -> dict:
    gross, swap = (result["gross_profit"], result["swap"]) if closing else (0.0, 0.0)
    return {
        "symbol": symbol, "trade_side": side, "volume": volume, "price": result["price"],
        "realized_pnl": gross + swap, "timestamp": timestamp, "status": "filled",
        "commission": result["commission"], "swap": swap,
        "order_id": result["order_id"], "position_id": result["position_id"],
    }


def _failure(symbol: str, side: str, volume: int, timestamp: str, error: str, status: str = "failed") -> dict:
    return {
        "symbol": symbol, "trade_side": side, "volume": volume, "price": None, "realized_pnl": 0.0,
        "timestamp": timestamp, "status": status, "commission": 0.0, "swap": 0.0, "error": error,
    }

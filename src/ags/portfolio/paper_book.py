import copy
import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from dataclasses import asdict

from ags.portfolio.book import Position

_UNITS_PER_VOLUME = 1 / 100
_ET = ZoneInfo("America/New_York")
_ROLLOVER_TIME = time(17, 0)


class PaperBook:
    """Simulated cTrader-shaped account. Pure: state in via the constructor, out via `state()`."""

    def __init__(self, state: dict | None = None, *, starting_equity: float = 10_000.0, stop_out_level: float = 0.5):
        if state is None:
            state = {"balance": starting_equity, "peak_equity": starting_equity, "positions": [], "next_position_id": 1}
        self._state = copy.deepcopy(state)
        self._stop_out_level = stop_out_level

    def state(self) -> dict:
        return copy.deepcopy(self._state)

    def apply_fills(self, deltas_lots: dict[str, float], quotes: dict, specs: dict, timestamp: str) -> list[dict]:
        fills = []
        for symbol, lots in deltas_lots.items():
            spec = specs[symbol]
            volume = int(abs(lots) * spec["lot_size"] / spec["step_volume"]) * spec["step_volume"]
            if volume < spec["min_volume"]:
                continue
            side = "BUY" if lots > 0 else "SELL"
            price = quotes[symbol]["ask" if side == "BUY" else "bid"]
            position = self._position(symbol)
            if position is not None and position["trade_side"] != side:
                closing = min(volume, position["volume"])
                fills.append(self._close(position, closing, price, timestamp))
                volume -= closing
            elif position is not None:
                total = position["volume"] + volume
                position["price"] = (position["price"] * position["volume"] + price * volume) / total
                position["volume"] = total
                fills.append(self._fill(symbol, side, volume, price, 0.0, timestamp))
                continue
            if volume > 0:
                self._state["positions"].append(
                    asdict(Position(self._state["next_position_id"], symbol, spec["symbol_id"], side, volume, price, timestamp))
                )
                self._state["next_position_id"] += 1
                fills.append(self._fill(symbol, side, volume, price, 0.0, timestamp))
        return fills

    @staticmethod
    def _fill(symbol, side, volume, price, realized, timestamp) -> dict:
        return {"symbol": symbol, "trade_side": side, "volume": volume, "price": price, "realized_pnl": realized, "timestamp": timestamp}

    def _position(self, symbol: str) -> dict | None:
        return next((p for p in self._state["positions"] if p["symbol_name"] == symbol), None)

    def _close(self, position: dict, volume: int, price: float, timestamp: str) -> dict:
        """Close `volume` of a position at `price`; realizes price P&L plus the closed share of swap."""
        units = volume * _UNITS_PER_VOLUME
        sign = 1 if position["trade_side"] == "BUY" else -1
        swap_share = position["swap"] * volume / position["volume"]
        realized = (price - position["price"]) * units * sign + swap_share
        self._state["balance"] += realized
        position["swap"] -= swap_share
        position["volume"] -= volume
        if position["volume"] == 0:
            self._state["positions"].remove(position)
        closing_side = "SELL" if sign == 1 else "BUY"
        return self._fill(position["symbol_name"], closing_side, volume, price, realized, timestamp)

    def rebase_peak(self, equity: float) -> None:
        self._state["peak_equity"] = equity

    def mark_to(self, quotes: dict) -> None:
        """Record the quote midpoints as the marks the next replay starts from."""
        self._state["marks"] = {s: (q["bid"] + q["ask"]) / 2 for s, q in quotes.items()}

    def accrue_swap(self, day: date, specs: dict, prices: dict | None = None) -> None:
        """Charge one rollover (17:00 ET on `day`) to every open position.

        There is no rollover at the weekend; the triple-swap weekday is charged
        three times. PIPS swap is pips of price per unit; PERCENTAGE is an
        annual percent of notional, per day.
        """
        if day.weekday() >= 5:
            return
        for p in self._state["positions"]:
            spec = specs[p["symbol_name"]]
            multiplier = 3 if calendar.day_name[day.weekday()].upper() == spec["triple_swap_day"] else 1
            rate = spec["swap_long"] if p["trade_side"] == "BUY" else spec["swap_short"]
            units = p["volume"] * _UNITS_PER_VOLUME
            if spec["swap_type"] == "PIPS":
                charge = rate * 10 ** -spec["pip_position"] * units
            else:
                price = (prices or {}).get(p["symbol_name"], p["price"])
                charge = rate / 100 / 365 * price * units
            p["swap"] += charge * multiplier

    def replay(self, h1_bars: dict[str, list[dict]], specs: dict, since: datetime, until: datetime) -> list[dict]:
        """Walk H1 bars (and the 17:00 ET rollovers) in (since, until], applying swap and stop-outs.

        Bars are processed in close-time order. Within a bar only that symbol's
        price moves, to its adverse extreme (low for longs, high for shorts).
        """
        timeline = []
        for symbol, bars in h1_bars.items():
            for bar in bars:
                close = datetime.fromisoformat(bar["timestamp"]) + timedelta(hours=1)
                if since < close <= until:
                    timeline.append((close, 1, symbol, bar))
        day = since.astimezone(_ET).date()
        while day <= until.astimezone(_ET).date():
            instant = datetime.combine(day, _ROLLOVER_TIME, tzinfo=_ET)
            if since < instant <= until:
                timeline.append((instant, 0, None, day))
            day += timedelta(days=1)

        events = []
        for instant, kind, symbol, payload in sorted(timeline, key=lambda t: (t[0], t[1])):
            if kind == 0:
                self.accrue_swap(payload, specs, prices=self._state.get("marks"))
            else:
                events.extend(self._replay_bar(symbol, payload, specs, instant))
        return events

    def _marks(self) -> dict:
        marks = self._state.setdefault("marks", {})
        for p in self._state["positions"]:
            marks.setdefault(p["symbol_name"], p["price"])
        return marks

    def _gap(self, specs: dict) -> float:
        """Equity minus the stop-out requirement at the current marks."""
        marks = self._marks()
        equity, margin = self._state["balance"], 0.0
        for p in self._state["positions"]:
            units = p["volume"] * _UNITS_PER_VOLUME
            sign = 1 if p["trade_side"] == "BUY" else -1
            equity += (marks[p["symbol_name"]] - p["price"]) * units * sign + p["swap"]
            margin += marks[p["symbol_name"]] * units * specs[p["symbol_name"]]["margin_rate"]
        return equity - self._stop_out_level * margin

    def _replay_bar(self, symbol: str, bar: dict, specs: dict, instant: datetime) -> list[dict]:
        events = []
        timestamp = instant.isoformat()
        while (moving := self._position(symbol)) is not None:
            marks = self._marks()
            current = marks[symbol]
            is_long = moving["trade_side"] == "BUY"
            target = min(current, bar["low"]) if is_long else max(current, bar["high"])
            gap_now = self._gap(specs)
            if gap_now >= 0:
                marks[symbol] = target
                gap_target = self._gap(specs)
                if gap_target >= 0:
                    break
                # The gap is linear in this symbol's price: find where it crosses zero.
                marks[symbol] = current + (target - current) * gap_now / (gap_now - gap_target)
            loser = min(self._state["positions"], key=lambda p: self._unrealized(p, marks[p["symbol_name"]]))
            price = marks[loser["symbol_name"]]
            fill = self._close(loser, loser["volume"], price, timestamp)
            events.append({**fill, "reason": "stop_out"})
        self._marks()[symbol] = bar["close"]
        return events

    def _unrealized(self, position: dict, price: float) -> float:
        sign = 1 if position["trade_side"] == "BUY" else -1
        return (price - position["price"]) * position["volume"] * _UNITS_PER_VOLUME * sign + position["swap"]

    def _pnl(self, position: dict, quote: dict) -> float:
        units = position["volume"] * _UNITS_PER_VOLUME
        if position["trade_side"] == "BUY":
            return (quote["bid"] - position["price"]) * units
        return (position["price"] - quote["ask"]) * units

    def snapshot(self, quotes: dict, specs: dict) -> dict:
        equity = self._state["balance"]
        used_margin = 0.0
        for p in self._state["positions"]:
            q = quotes[p["symbol_name"]]
            equity += self._pnl(p, q) + p["swap"]
            used_margin += (q["bid"] + q["ask"]) / 2 * p["volume"] * _UNITS_PER_VOLUME * specs[p["symbol_name"]]["margin_rate"]
        self._state["peak_equity"] = max(self._state["peak_equity"], equity)
        return {
            "equity": equity,
            "used_margin": used_margin,
            "free_margin": equity - used_margin,
            "peak_equity": self._state["peak_equity"],
        }

import json
from datetime import datetime, timedelta

import pandas as pd

from ags.portfolio.stores import active_manager_store
from ags.storage import Storage
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES


def load_cycles(storage: Storage, book: str, *, now: datetime, days: int | None = None) -> pd.DataFrame:
    """Cycle files of one book, oldest first, with their `as_of` as a UTC `at` column; `days` keeps the last N.

    `book` is "manager" or "shadow"; "manager" is whichever manager store is in use (the demo one once it has records).
    """
    store = active_manager_store(storage) if book == "manager" else book
    records = [json.loads(storage.get(k)) for k in storage.list(f"portfolio/{store}/cycles/")]
    frame = pd.DataFrame(records, columns=None if records else ["at"])
    if frame.empty:
        return frame
    frame["at"] = pd.to_datetime(frame["as_of"], utc=True)
    frame = frame.sort_values("at").reset_index(drop=True)
    return frame if days is None else frame[frame["at"] > now - timedelta(days=days)].reset_index(drop=True)


def max_drawdown(curve: list[float]) -> dict:
    """Largest peak-to-trough fall of a closing-equity curve, and the fall from the running peak now."""
    peak, worst, worst_pct = float("-inf"), 0.0, 0.0
    for equity in curve:
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
        worst_pct = max(worst_pct, (peak - equity) / peak * 100 if peak else 0.0)
    current = peak - curve[-1] if curve else 0.0
    return {
        "max": worst, "max_pct": worst_pct,
        "current": current, "current_pct": current / peak * 100 if curve and peak else 0.0,
    }


def equity_frame(storage: Storage, *, now: datetime, days: int | None = None) -> pd.DataFrame:
    """Both books' closing equity per shared cycle, and their gap."""
    manager, shadow = (load_cycles(storage, b, now=now, days=days) for b in ("manager", "shadow"))
    if manager.empty or shadow.empty:
        return pd.DataFrame(columns=["at", "manager", "shadow", "gap"])
    joined = manager.merge(shadow, on="cycle_id", suffixes=("_m", "_s"))
    # The gap is each book's change since the manager's first cycle, so a manager that started later
    # (the demo one, from its own capital) is compared with the shadow over the same stretch.
    offset = _first_gap(storage, now)
    return pd.DataFrame({
        "at": joined["at_m"], "manager": joined["equity_m"], "shadow": joined["equity_s"],
        "gap": joined["equity_m"] - joined["equity_s"] - offset,
    })


def _first_gap(storage: Storage, now: datetime) -> float:
    """Manager minus shadow opening equity at the manager's first shared cycle (0 when unknown)."""
    manager, shadow = (load_cycles(storage, b, now=now) for b in ("manager", "shadow"))
    if manager.empty or shadow.empty or "opening_equity" not in manager or "opening_equity" not in shadow:
        return 0.0
    joined = manager.merge(shadow, on="cycle_id", suffixes=("_m", "_s"))
    return 0.0 if joined.empty else float(joined.iloc[0]["opening_equity_m"] - joined.iloc[0]["opening_equity_s"])


COMMODITIES = list(CTRADER_SYMBOL_NAMES)
# cTrader volume is in 1/100 of a unit (portfolio/book.py).
_UNITS_PER_VOLUME = 1 / 100


def _usd(state: dict) -> dict[str, float]:
    """Signed USD value of each held commodity at the cycle's marks; shorts are negative."""
    return {
        p["symbol_name"]: (1 if p["trade_side"] == "BUY" else -1) * p["volume"] * _UNITS_PER_VOLUME * state["marks"][p["symbol_name"]]
        for p in state["positions"]
    }


def positions_frame(cycles: pd.DataFrame, *, unit: str) -> pd.DataFrame:
    """Signed position per commodity per cycle in `unit` ("lots" or "usd"), plus gross (sum of absolute values)."""
    columns = ["at", *COMMODITIES, "gross"]
    if cycles.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for _, cycle in cycles.iterrows():
        held = cycle["closing_lots"] if unit == "lots" else _usd(cycle["closing_state"])
        values = {c: float(held.get(c, 0.0)) for c in COMMODITIES}
        rows.append({"at": cycle["at"], **values, "gross": sum(abs(v) for v in values.values())})
    return pd.DataFrame(rows, columns=columns)


def margin_frame(storage: Storage, *, now: datetime, days: int | None = None) -> pd.DataFrame:
    """Margin utilization (used margin / equity, percent) of both books per shared cycle."""
    manager, shadow = (load_cycles(storage, b, now=now, days=days) for b in ("manager", "shadow"))
    if manager.empty or shadow.empty:
        return pd.DataFrame(columns=["at", "manager", "shadow"])
    joined = manager.merge(shadow, on="cycle_id", suffixes=("_m", "_s"))

    def pct(used: pd.Series, equity: pd.Series) -> pd.Series:
        return (used / equity.where(equity != 0) * 100).fillna(0.0)

    return pd.DataFrame({
        "at": joined["at_m"], "manager": pct(joined["used_margin_m"], joined["equity_m"]),
        "shadow": pct(joined["used_margin_s"], joined["equity_s"]),
    })


def calls_frame(cycles: pd.DataFrame) -> pd.DataFrame:
    """One row per commodity per cycle: the coordinator's call and conviction ("failed", 0 where the run failed).

    `until` is when the call stops being the latest: the next cycle, or half a day on for the newest.
    """
    columns = ["at", "until", "commodity", "call", "conviction"]
    rows = []
    for i, cycle in cycles.iterrows():
        until = cycles["at"].iloc[i + 1] if i + 1 < len(cycles) else cycle["at"] + timedelta(hours=12)
        for commodity in COMMODITIES:
            call = cycle["calls"].get(commodity)
            rows.append({
                "at": cycle["at"], "until": until, "commodity": commodity,
                "call": call["call"] if call else "failed", "conviction": call["conviction"] if call else 0,
            })
    return pd.DataFrame(rows, columns=columns)


def _unrealized(state: dict) -> dict[str, float]:
    """Open P&L of each held commodity at the cycle's marks, including the swap accrued so far."""
    return {
        p["symbol_name"]: (state["marks"][p["symbol_name"]] - p["price"]) * p["volume"] * _UNITS_PER_VOLUME
        * (1 if p["trade_side"] == "BUY" else -1) + p["swap"]
        for p in state["positions"]
    }


def pnl_frame(cycles: pd.DataFrame, *, now: datetime | None = None, days: int | None = None) -> pd.DataFrame:
    """Cumulative P&L per commodity per cycle: every realized fill (stop-outs included) plus open P&L at the marks.

    Summed over all `cycles` so the last `days` start from what was already realized, not from zero.
    """
    columns = ["at", *COMMODITIES, "total"]
    if cycles.empty:
        return pd.DataFrame(columns=columns)
    realized = dict.fromkeys(COMMODITIES, 0.0)
    rows = []
    for _, cycle in cycles.iterrows():
        for fill in [*cycle["fills"], *cycle["stop_outs"]]:
            realized[fill["symbol"]] += fill["realized_pnl"]
        open_pnl = _unrealized(cycle["closing_state"])
        values = {c: realized[c] + open_pnl.get(c, 0.0) for c in COMMODITIES}
        rows.append({"at": cycle["at"], **values, "total": sum(values.values())})
    frame = pd.DataFrame(rows, columns=columns)
    return frame if days is None or now is None else frame[frame["at"] > now - timedelta(days=days)].reset_index(drop=True)


def daily_totals(rows: list[dict], *, now: datetime, days: int | None = None) -> pd.DataFrame:
    """Spend per UTC day with its running total, over the last `days`; days without any cost are left out."""
    columns = ["day", "total", "cumulative"]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    if days is not None:
        frame = frame[frame["day"] >= (now - timedelta(days=days)).date().isoformat()]
    totals = frame.groupby("day", as_index=False)["cost"].sum().rename(columns={"cost": "total"})
    totals = totals[totals["total"] > 0].sort_values("day").reset_index(drop=True)
    totals["cumulative"] = totals["total"].cumsum()
    return totals[columns]

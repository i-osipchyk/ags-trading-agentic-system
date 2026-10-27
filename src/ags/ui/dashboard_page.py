from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import altair as alt
import pandas as pd
import streamlit as st

from ags.notify.spend import daily_spend
from ags.storage import LocalStorage
from ags.ui.dashboard import (
    COMMODITIES,
    calls_frame,
    daily_totals,
    equity_frame,
    load_cycles,
    margin_frame,
    max_drawdown,
    pnl_frame,
    positions_frame,
)
from ags.ui.jobs import current_job

_RANGES = {"All": None, "30d": 30, "7d": 7}
_CALL_COLORS = alt.Scale(domain=["bullish", "neutral", "bearish", "failed"], range=["#2e7d32", "#9e9e9e", "#c62828", "#ff9800"])
_X = alt.X("at:T", title="UTC", axis=alt.Axis(format="%m-%d %H:%M"))


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _drawdown(label: str, curve: list[float]) -> None:
    dd = max_drawdown(curve)
    left, right = st.columns(2)
    left.metric(f"{label} max drawdown", f"{_money(dd['max'])} ({dd['max_pct']:.1f}%)")
    right.metric(f"{label} current drawdown", f"{_money(dd['current'])} ({dd['current_pct']:.1f}%)")


@st.fragment(run_every=3)
def _job_banner(jobs_dir: Path) -> None:
    job = current_job(jobs_dir)
    if job is None:
        return
    if job["status"] == "running":
        st.info(f"Running: {job['label']} (started {job['started_at']})")
    elif job["status"] == "done":
        st.success(f"Done: {job['label']}")
    else:
        st.error(f"Failed: {job['label']}")
        st.code(job["output_tail"])
    if job["status"] != "running" and st.session_state.get("_dash_job_was_running"):
        st.session_state["_dash_job_was_running"] = False
        st.rerun()
    st.session_state["_dash_job_was_running"] = job["status"] == "running"


def _equity_panel(equity: pd.DataFrame) -> None:
    st.subheader("Equity")
    _drawdown("Manager", list(equity["manager"]))
    _drawdown("Shadow", list(equity["shadow"]))
    lines = equity.melt("at", ["manager", "shadow"], var_name="book", value_name="equity")
    chart = alt.Chart(lines).mark_line().encode(_X, alt.Y("equity:Q", scale=alt.Scale(zero=False)), color="book:N")
    st.altair_chart(chart, width="stretch")
    st.markdown("**Manager − shadow**")
    st.altair_chart(alt.Chart(equity).mark_area(opacity=0.5).encode(_X, alt.Y("gap:Q", title="USD")), width="stretch")


def _positions_panel(cycles: dict[str, pd.DataFrame]) -> None:
    st.subheader("Positions")
    book = st.radio("Book", ["manager", "shadow"], horizontal=True, key="positions_book")
    unit = st.radio("Unit", ["lots", "usd"], horizontal=True, key="positions_unit")
    frame = positions_frame(cycles[book], unit=unit)
    long = frame.melt("at", [*COMMODITIES, "gross"], var_name="commodity", value_name="value")
    lines = alt.Chart(long).mark_line(interpolate="step-after").encode(
        _X, alt.Y("value:Q", title=unit), color=alt.Color("commodity:N", sort=[*COMMODITIES, "gross"]),
        strokeWidth=alt.condition(alt.datum.commodity == "gross", alt.value(3), alt.value(1.5)),
    )
    st.altair_chart(lines, width="stretch")
    st.caption("Signed per commodity (shorts negative); the thick line is gross exposure, the sum of absolute positions.")
    st.markdown("**Calls**")
    calls = calls_frame(cycles["manager"])
    grid = alt.Chart(calls).mark_rect().encode(
        _X, alt.X2("until:T"), alt.Y("commodity:N", sort=COMMODITIES, title=None), color=alt.Color("call:N", scale=_CALL_COLORS),
        opacity=alt.Opacity("conviction:Q", scale=alt.Scale(domain=[0, 5], range=[0.35, 1.0]), legend=None),
        tooltip=["at:T", "until:T", "commodity:N", "call:N", "conviction:Q"],
    )
    st.altair_chart(grid, width="stretch")


def _margin_panel(margin: pd.DataFrame) -> None:
    st.subheader("Margin utilization")
    lines = margin.melt("at", ["manager", "shadow"], var_name="book", value_name="pct")
    y = alt.Y("pct:Q", title="used margin / equity, %", scale=alt.Scale(domain=[0, 100], clamp=True))
    st.altair_chart(alt.Chart(lines).mark_line(clip=True).encode(_X, y, color="book:N"), width="stretch")


def _pnl_panel(full: dict[str, pd.DataFrame], *, now: datetime, days: int | None) -> None:
    st.subheader("P&L by commodity")
    book = st.radio("Book", ["manager", "shadow"], horizontal=True, key="pnl_book")
    pnl = pnl_frame(full[book], now=now, days=days)
    lines = pnl.melt("at", [*COMMODITIES, "total"], var_name="commodity", value_name="pnl")
    chart = alt.Chart(lines).mark_line().encode(
        _X, alt.Y("pnl:Q", title="cumulative USD"), color=alt.Color("commodity:N", sort=[*COMMODITIES, "total"]),
        strokeWidth=alt.condition(alt.datum.commodity == "total", alt.value(3), alt.value(1.5)),
    )
    st.altair_chart(chart, width="stretch")
    st.caption("Realized fills (swap included) plus open P&L at the cycle's marks; spread and commission are not in the marks.")


def _spend_panel(storage: LocalStorage, *, now: datetime, days: int | None) -> None:
    st.subheader("LLM spend per day")
    by = st.radio("Colour by", ["commodity", "agent"], horizontal=True, key="spend_by")
    rows = daily_spend(storage, by=by)
    totals = daily_totals(rows, now=now, days=days)
    if totals.empty:
        st.caption("No priced LLM calls in the local logs for this range.")
        return
    spent = pd.DataFrame([r for r in rows if r["day"] in set(totals["day"])])
    day = alt.X("day:O", title="UTC day")  # ordinal: only days that cost something get a bar
    bars = alt.Chart(spent).mark_bar(size=24).encode(
        day, alt.Y("cost:Q", title="USD per day"), color=alt.Color("key:N", title=by),
        tooltip=["day:O", "key:N", alt.Tooltip("cost:Q", format="$.2f")],
    )
    line = alt.Chart(totals).mark_line(color="#111111", point=True).encode(
        day, alt.Y("cumulative:Q", title="cumulative USD", axis=alt.Axis(orient="right")),
        tooltip=["day:O", alt.Tooltip("total:Q", format="$.2f"), alt.Tooltip("cumulative:Q", format="$.2f")],
    )
    st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent"), width="stretch")


def render_dashboard_page(
    *, root: Path, jobs_dir: Path, pull: Callable[[], None],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    current = now()
    storage = LocalStorage(root)
    st.title("Dashboard")

    pull_col, range_col = st.columns([1, 2])
    if pull_col.button("Pull latest data"):
        pull()
        st.rerun()
    days = _RANGES[range_col.radio("Range", list(_RANGES), horizontal=True, key="range")]
    _job_banner(jobs_dir)

    full = {book: load_cycles(storage, book, now=current) for book in ("manager", "shadow")}
    cycles = {book: load_cycles(storage, book, now=current, days=days) for book in ("manager", "shadow")}
    equity = equity_frame(storage, now=current, days=days)
    if equity.empty:
        st.info("No cycles in the local mirror for this range. Pull the latest data, or widen the range.")
        return
    st.caption(f"Newest cycle: {equity['at'].iloc[-1]:%Y-%m-%d %H:%M} UTC · {len(equity)} cycles in range")

    _equity_panel(equity)
    _pnl_panel(full, now=current, days=days)
    _positions_panel(cycles)
    _margin_panel(margin_frame(storage, now=current, days=days))
    _spend_panel(storage, now=current, days=days)

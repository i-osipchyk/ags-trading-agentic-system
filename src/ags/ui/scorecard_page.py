from datetime import date
from pathlib import Path
from typing import Callable

import altair as alt
import pandas as pd
import streamlit as st

from ags import scoring
from ags.ui.dashboard_page import job_banner

_BASES = {"Raw": False, "Market-neutral": True}
_VERDICT = {
    "pass": ("success", "Pass"),
    "fail": ("error", "Fail"),
    "not_yet": ("info", "Not yet testable"),
}
_PCT = {"mean_return": "{:.2%}", "ci_low": "{:.2%}", "ci_high": "{:.2%}", "hit_rate": "{:.0%}", "effective": "{:.1f}"}


@st.cache_data(ttl=60, show_spinner="Scoring calls")
def _rows(log_dir: str, data_dir: str, horizon_days: int, since: date | None, mirror: tuple) -> pd.DataFrame:
    # `mirror` (newest log, newest close) is only a cache key: a pull changes it, so a pull is never answered from cache.
    return scoring.build_rows(Path(log_dir), Path(data_dir), horizon_days=horizon_days, since=since)


@st.cache_data(ttl=60, show_spinner="Scoring the challenger")
def _matched(log_dir: str, data_dir: str, horizon_days: int, since: date | None, mirror: tuple, batch: str,
             batch_stamp: float) -> tuple[pd.DataFrame, dict]:
    # `batch_stamp` (the newest challenger log's mtime) is only a cache key, so a finished batch is never stale.
    frame = scoring.build_rows(Path(log_dir), Path(data_dir), horizon_days=horizon_days, since=since)
    if frame.empty:
        return pd.DataFrame(), {"official": 0, "matched": 0, "missing": 0}
    return scoring.compare_challenger(frame, Path(batch) / "logs")


def _verdict_panel(week: pd.DataFrame) -> None:
    bar = scoring.PASS_BAR
    result = scoring.evaluate_pass_bar(week)
    kind, label = _VERDICT[result["verdict"]]
    getattr(st, kind)(f"**{label}** against the pass bar. " + ("; ".join(result["reasons"]) or "The bar is met."))
    left, mid, right = st.columns(3)
    left.metric("Effective weeks", f"{result['effective_weeks']:.1f} / {bar.min_effective_weeks:g}")
    mid.metric("Weeks the basket did not fall", f"{result['non_falling_weeks']} / {bar.min_non_falling_weeks}")
    if result["difference"] is not None:
        low, high = result["interval"]
        right.metric("Coordinator − trend rule (1 week, neutral)", f"{result['difference']:+.2%}",
                     help=f"{bar.confidence:.0%} interval {low:+.2%} to {high:+.2%}. The bar needs the interval above zero.")
    st.caption(
        f"The bar is fixed in `ags.scoring.PASS_BAR`: beat the trend rule market-neutral at 1 week with the "
        f"{bar.confidence:.0%} interval above zero, over {bar.min_effective_weeks:g} effective weeks, at least "
        f"{bar.min_non_falling_weeks} of them not falling."
    )


def _summary_panel(frame: pd.DataFrame, *, neutral: bool) -> None:
    summary = scoring.summarize(frame, neutral=neutral)
    if summary.empty:
        st.caption("No scored calls.")
        return
    bars = alt.Chart(summary).mark_bar().encode(
        x=alt.X("strategy:N", sort=list(scoring.STRATEGIES), title=None),
        y=alt.Y("mean_return:Q", title="weighted mean return", axis=alt.Axis(format="%")),
        color=alt.Color("strategy:N", legend=None),
    )
    ranges = alt.Chart(summary).mark_rule(color="#111111").encode(x="strategy:N", y="ci_low:Q", y2="ci_high:Q")
    st.altair_chart(bars + ranges, width="stretch")
    st.dataframe(summary.style.format(_PCT), hide_index=True, width="stretch")
    st.caption(
        "Each commodity counts once per calendar week: its calls in that week share a weight of 1, and a week with "
        f"fewer than the scheduled {scoring.EXPECTED_CALLS_PER_WEEK} calls is worth its share (effective = "
        "commodity-weeks, the weighted sample size). Hit rate counts calls that took a side; mean return counts all calls, a neutral "
        "call earning 0. Bars show the 90% interval over whole commodity-weeks."
    )


def _breakdown_panels(frame: pd.DataFrame, *, neutral: bool) -> None:
    fmt = {**{s: "{:.2%}" for s in scoring.STRATEGIES}, "coordinator_hit_rate": "{:.0%}", "effective": "{:.1f}"}
    st.markdown("**By week of the call**")
    weekly = scoring.breakdown(frame, "week", neutral=neutral)
    st.dataframe(weekly.style.format(fmt), hide_index=True, width="stretch")
    st.markdown("**By commodity**")
    st.dataframe(scoring.breakdown(frame, "commodity", neutral=neutral).style.format(fmt), hide_index=True, width="stretch")
    st.markdown("**By code and prompt version**")
    st.dataframe(scoring.breakdown(frame, "version", neutral=neutral).style.format(fmt), hide_index=True, width="stretch")
    st.markdown("**Coordinator by conviction**")
    st.dataframe(
        scoring.by_conviction(frame, neutral=neutral).style.format(
            {"hit_rate": "{:.0%}", "mean_return": "{:.2%}", "effective": "{:.1f}"}
        ),
        hide_index=True, width="stretch",
    )
    st.caption("Directional calls only. Calls are scored as a full position whatever their conviction.")
    st.markdown("**Coordinator by direction**")
    st.dataframe(
        scoring.by_direction(frame, neutral=neutral).style.format(
            {"hit_rate": "{:.0%}", "mean_return": "{:.2%}", "effective": "{:.1f}"}
        ),
        hide_index=True, width="stretch",
    )


_LABELS = {"coordinator": "challenger", "official": "official (live cycle)"}


def _challenger_section(
    *, log_dir: Path, data_dir: Path, root: Path, horizon: str, since: date | None, mirror: tuple, neutral: bool
) -> None:
    st.header("Challenger: aligned analysts")
    batches = sorted(p for p in Path(root).glob("*") if (p / "logs").is_dir()) if Path(root).is_dir() else []
    if not batches:
        st.caption("No challenger runs yet. `uv run python scripts/run_challenger.py` prints the plan and `--run` makes it.")
        return
    batch = st.selectbox("Batch", batches, format_func=lambda p: p.name, key="score_batch")
    stamp = max((f.stat().st_mtime for f in (batch / "logs").glob("*.jsonl")), default=0.0)
    matched, info = _matched(str(log_dir), str(data_dir), scoring.HORIZONS[horizon], since, mirror, str(batch), stamp)
    st.caption(
        f"The challenger re-made {info['matched']} of {info['official']} official runs in range; the {info['missing']} "
        "missing ones (not run yet, or no call) are left out of both sides, so the two are scored on identical cycles."
    )
    if matched.empty:
        return

    st.subheader(f"Challenger vs the live cycle · {horizon} · {'market-neutral' if neutral else 'raw'}")
    summary = scoring.summarize(matched, neutral=neutral, strategies=scoring.COMPARED)
    summary["strategy"] = summary["strategy"].replace(_LABELS)
    st.dataframe(summary.style.format(_PCT), hide_index=True, width="stretch")
    diff = scoring.challenger_difference(matched, neutral=neutral)
    left, right = st.columns(2)
    if diff["difference"] is not None:
        low, high = diff["interval"]
        left.metric("Challenger − official, per call", f"{diff['difference']:+.3%}",
                    help=f"90% interval {low:+.3%} to {high:+.3%}. An interval that spans zero is not a difference.")
    same = scoring.agreement(matched)
    right.metric("Same call as the official run", f"{same['same_call']:.0%}",
                 help=f"Opposite direction {same['opposite_call']:.0%}; one of the two neutral and the other not "
                      f"{same['neutral_in_one']:.0%}.")

    st.markdown("**Each analyst's bias, scored as a position**")
    st.dataframe(
        scoring.by_analyst(matched, neutral=neutral).style.format(
            {"hit_rate": "{:.0%}", "mean_return": "{:.2%}", "effective": "{:.1f}"}),
        hide_index=True, width="stretch",
    )
    st.caption("Position = the sign of the bias (0 is no view). \"technical (code only)\" is the mechanical score the "
               "model started from, so the difference to the technical row is what its adjustments did.")

    week, _ = _matched(str(log_dir), str(data_dir), scoring.VERDICT_HORIZON, since, mirror, str(batch), stamp)
    if not week.empty:
        st.markdown("**Challenger against the pass bar (1 week, market-neutral, matched cycles)**")
        _verdict_panel(week)


def render_scorecard_page(
    *, log_dir: Path, data_dir: Path, jobs_dir: Path, pull: Callable[[], None], challenger_root: Path | None = None
) -> None:
    st.title("Scorecard")
    st.caption(
        "The official coordinator calls, scored from the local mirror against mechanical baselines. "
        "Read-only: it runs no model and writes nothing."
    )
    if st.button("Pull latest data"):
        pull()
        st.rerun()
    job_banner(jobs_dir)
    through = scoring.price_data_through(data_dir)
    newest_log = max((p.stem.rsplit("_", 1)[-1][:10] for p in Path(log_dir).glob("*.jsonl")), default=None)
    if through is None or newest_log is None:
        st.info("No logs or daily closes in the local mirror. Pull the latest data on the Dashboard first.")
        return
    if through.isoformat() < newest_log:
        st.warning(
            f"Daily closes run through {through} but calls go to {newest_log}: calls whose exit close is missing "
            "are left out. Sync `data` to include them."
        )

    mirror = (newest_log, str(through))
    left, mid, right = st.columns(3)
    horizon = left.radio("Horizon", list(scoring.HORIZONS), index=1, horizontal=True, key="score_horizon")
    basis = mid.radio("Returns", list(_BASES), horizontal=True, key="score_basis")
    since = right.date_input(
        "Since (the clean start date)", value=scoring.REVIEW_START, key="score_since",
        help="Calls before this date are left out. Clear it to include every logged call, dev runs too.",
    )
    neutral = _BASES[basis]

    week = _rows(str(log_dir), str(data_dir), scoring.VERDICT_HORIZON, since, mirror)
    st.subheader("Pass bar")
    _verdict_panel(week)

    frame = _rows(str(log_dir), str(data_dir), scoring.HORIZONS[horizon], since, mirror)
    st.subheader(f"{horizon} horizon · {basis.lower()}")
    if frame.empty:
        reason = f"Daily closes in the mirror stop at {through}" if since is None or since > through else "No call in this range has an exit close yet"
        st.info(
            f"{reason}, so no call from {since or 'the start'} has its exit close "
            f"({scoring.HORIZONS[horizon]} trading day(s) from the call day). "
            "Pull the latest data on the Dashboard, or choose an earlier date."
        )
        return
    st.caption(f"{len(frame)} scored calls from {frame['triggered_at'].min():%Y-%m-%d} to {frame['triggered_at'].max():%Y-%m-%d}.")
    _summary_panel(frame, neutral=neutral)
    _breakdown_panels(frame, neutral=neutral)
    if challenger_root is not None:
        _challenger_section(
            log_dir=log_dir, data_dir=data_dir, root=challenger_root, horizon=horizon, since=since, mirror=mirror,
            neutral=neutral,
        )

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import streamlit as st

from ags.agents.auditor import CHECKPOINT_WEEKS, find_due_checkpoints
from ags.ui.jobs import (
    COMMODITIES,
    JobRunning,
    audit_commands,
    cadence,
    current_job,
    needs_confirmation,
    pipeline_commands,
    produced_run_ids,
    start_job,
)
from ags.ui.runs import build_grid, format_run_id
from ags.ui.streamlit_app import coordinator_call, render_call

_ICONS = {"pending": "🟡", "due": "🔴", "graded": "🟢", "no_call": "⚪"}
_MARKS = {True: " ✓", False: " ✗", None: " –"}
_CADENCE_NOTES = {
    "off_cadence": "Today isn't a Friday (ET): this run will be flagged off-cadence.",
    "weekend": "It's the weekend (ET): markets are closed, so inputs are stale. This run will be flagged off-cadence.",
}


def _cell_label(cell: dict) -> str:
    label = _ICONS[cell["state"]]
    return label + _MARKS[cell["direction_correct"]] if cell["state"] == "graded" else label


def _render_result(log_dir: Path, output: str) -> None:
    run_ids = produced_run_ids(output)
    if not run_ids:
        st.text(output)  # the auditor's short graded summary
    for run_id in run_ids:
        call = coordinator_call(log_dir, run_id)
        st.markdown(f"**{format_run_id(run_id)}**")
        if call and call.get("call"):
            render_call(call)
        else:
            st.warning(f"No call: {(call or {}).get('error', 'coordinator output missing')}")


@st.fragment(run_every=3)
def _job_banner(log_dir: Path, jobs_dir: Path) -> None:
    job = current_job(jobs_dir, tail_lines=300)
    if job is None:
        return
    if job["status"] == "running":
        st.info(f"Running: {job['label']} (started {job['started_at']})")
    elif job["status"] == "done":
        st.success(f"Done: {job['label']}")
        _render_result(log_dir, job["output_tail"])
    else:
        st.error(f"Failed: {job['label']}")
        st.code(job["output_tail"])  # the error message
    if job["status"] != "running" and st.session_state.get("_job_was_running"):
        st.session_state["_job_was_running"] = False
        st.rerun()
    st.session_state["_job_was_running"] = job["status"] == "running"


def _launch(jobs_dir: Path, commands: list[list[str]], label: str, now: datetime) -> None:
    try:
        start_job(jobs_dir, commands, label=label, now=now)
    except JobRunning as exc:
        st.warning(f"A job is already running: {exc}")
    st.session_state.pop("pending", None)
    st.rerun()


def _request(action: str, label: str, commands: list[list[str]], now: datetime, jobs_dir: Path) -> None:
    if needs_confirmation(action, now):
        st.session_state["pending"] = {"label": label, "commands": commands}
        st.rerun()
    _launch(jobs_dir, commands, label, now)


def render_runs_page(*, log_dir: Path, audit_dir: Path, jobs_dir: Path, open_run: Callable[[], None], now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
    current = now()
    st.title("Runs")
    _job_banner(log_dir, jobs_dir)
    job = current_job(jobs_dir)
    busy = job is not None and job["status"] == "running"

    pending = st.session_state.get("pending")
    if pending:
        st.warning(f"Confirm: {pending['label']} ({len(pending['commands'])} command(s)). This is permanent and uses LLM calls.")
        yes, no = st.columns(2)
        if yes.button("Confirm", disabled=busy):
            _launch(jobs_dir, pending["commands"], pending["label"], current)
        if no.button("Cancel"):
            st.session_state.pop("pending")
            st.rerun()

    st.subheader("Run pipeline")
    note = _CADENCE_NOTES.get(cadence(current))
    if note:
        st.caption(note)
    pick, run_one, run_all = st.columns([2, 1, 1])
    commodity = pick.selectbox("Commodity", COMMODITIES, label_visibility="collapsed")
    if run_one.button(f"Run {commodity}", disabled=busy):
        _request("run_one", f"pipeline: {commodity}", pipeline_commands([commodity]), current, jobs_dir)
    if run_all.button(f"Run all {len(COMMODITIES)}", disabled=busy):
        _request("run_all", "pipeline: all commodities", pipeline_commands(list(COMMODITIES)), current, jobs_dir)

    due = find_due_checkpoints(log_dir, audit_dir, now=current)
    if st.button(f"Grade all due ({len(due)})", disabled=busy or not due):
        _request("audit_all", "auditor: all due checkpoints", audit_commands(), current, jobs_dir)

    st.subheader("Runs")
    chosen = st.multiselect("Commodity", COMMODITIES, placeholder="All commodities")
    dates = st.date_input("Triggered between", value=(), format="YYYY-MM-DD")
    start, end = (dates + (None, None))[:2] if isinstance(dates, tuple) else (None, None)
    rows = build_grid(log_dir, audit_dir, now=current, commodities=chosen or None, start=start, end=end)
    if not rows:
        st.info("No runs match.")
        return

    widths = [3, 2, 1] + [1] * len(CHECKPOINT_WEEKS)
    header = st.columns(widths)
    for col, text in zip(header, ["Run", "Call", "", *[f"{w}w" for w in CHECKPOINT_WEEKS]]):
        col.markdown(f"**{text}**")
    for row in rows:
        cols = st.columns(widths)
        if cols[0].button(format_run_id(row["run_id"]), key=f"open-{row['run_id']}"):
            st.session_state["selected_run"] = row["run_id"]
            open_run()
        cols[1].write(row["call"] or "no call")
        cols[2].write("off-cadence" if row["off_cadence"] else "")
        for col, weeks in zip(cols[3:], CHECKPOINT_WEEKS):
            cell = row["cells"][weeks]
            if cell["state"] == "due":
                if col.button("🔴", key=f"grade-{row['run_id']}-{weeks}", disabled=busy, help="Due: click to grade"):
                    _request("audit_one", f"auditor: {format_run_id(row['run_id'])} {weeks}w", audit_commands(run_id=row["run_id"], weeks=weeks), current, jobs_dir)
            else:
                col.write(_cell_label(cell))

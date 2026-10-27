"""Streamlit app: dashboard (paper-book history), runs grid with run/audit controls, plus read-only chat over frozen runs (localhost only).

Launch with `uv run streamlit run run_chat.py`. Chat has zero tool access and
never writes to the run logs. Runs and audits are triggered by spawning the
existing CLIs (run_pipeline.py / run_auditor.py) as subprocesses.
"""

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from ags.config import Config
from ags.llm.client import LLMClient
from ags.ui.dashboard_page import render_dashboard_page
from ags.ui.jobs import JobRunning, start_job, sync_commands
from ags.ui.runs_page import render_runs_page
from ags.ui.streamlit_app import render_chat_page

_ROOT = Path(__file__).resolve().parents[1]  # repo root


@st.cache_resource
def _client_and_model() -> tuple[LLMClient, str]:
    config = Config.from_env()
    return LLMClient(), config.deepseek_model


def _runs() -> None:
    render_runs_page(log_dir=_ROOT / "logs", audit_dir=_ROOT / "audits", jobs_dir=_ROOT / "jobs", open_run=lambda: st.switch_page(_detail))


def _run_detail() -> None:
    chat_client, model = _client_and_model()
    render_chat_page(log_dir=_ROOT / "logs", chat_client=chat_client, model=model)


def _dashboard() -> None:
    def pull() -> None:
        try:
            start_job(_ROOT / "jobs", sync_commands(), label="Pull latest data", now=datetime.now(timezone.utc))
        except JobRunning as exc:
            st.warning(f"A job is already running: {exc}")

    render_dashboard_page(root=_ROOT, jobs_dir=_ROOT / "jobs", pull=pull)


st.set_page_config(page_title="Ags", layout="wide")
_detail = st.Page(_run_detail, title="Run detail", url_path="detail")
st.navigation([
    st.Page(_dashboard, title="Dashboard", url_path="dashboard", default=True),
    st.Page(_runs, title="Runs", url_path="runs"),
    _detail,
]).run()

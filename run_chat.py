"""Streamlit app: runs grid with run/audit controls, plus read-only chat over frozen runs (localhost only).

Launch with `uv run streamlit run run_chat.py`. Chat has zero tool access and
never writes to the run logs. Runs and audits are triggered by spawning the
existing CLIs (run_pipeline.py / run_auditor.py) as subprocesses.
"""

from pathlib import Path

import streamlit as st

from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient
from ags.ui.runs_page import render_runs_page
from ags.ui.streamlit_app import render_chat_page

_ROOT = Path(__file__).parent


@st.cache_resource
def _client_and_model() -> tuple[DeepSeekChatClient, str]:
    config = Config.from_env()
    return DeepSeekChatClient(config), config.deepseek_model


def _runs() -> None:
    render_runs_page(log_dir=_ROOT / "logs", audit_dir=_ROOT / "audits", jobs_dir=_ROOT / "jobs", open_run=lambda: st.switch_page(_detail))


def _run_detail() -> None:
    chat_client, model = _client_and_model()
    render_chat_page(log_dir=_ROOT / "logs", chat_client=chat_client, model=model)


st.set_page_config(page_title="Ags runs", layout="wide")
_detail = st.Page(_run_detail, title="Run detail", url_path="detail")
st.navigation([st.Page(_runs, title="Runs", url_path="runs", default=True), _detail]).run()

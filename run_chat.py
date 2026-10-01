"""Streamlit app: read-only chat over frozen runs (localhost only).

Launch with `uv run streamlit run run_chat.py`. Chat has zero tool access and
never writes to the run logs.
"""

from pathlib import Path

import streamlit as st

from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient
from ags.ui.streamlit_app import render_chat_page

_ROOT = Path(__file__).parent


@st.cache_resource
def _client_and_model() -> tuple[DeepSeekChatClient, str]:
    config = Config.from_env()
    return DeepSeekChatClient(config), config.deepseek_model


st.set_page_config(page_title="Ags run chat", layout="centered")
_chat_client, _model = _client_and_model()
render_chat_page(log_dir=_ROOT / "logs", chat_client=_chat_client, model=_model)

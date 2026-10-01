from pathlib import Path

import streamlit as st

from ags.llm.loop import ChatClient
from ags.logging.run_log import RunLog
from ags.ui.chat import answer_question


def _timestamp(run_id: str) -> str:
    return run_id.rsplit("_", 1)[-1]


def _coordinator_call(log_dir: Path, run_id: str) -> dict | None:
    outputs = [e["payload"] for e in RunLog(log_dir, run_id).read() if e["agent"] == "coordinator" and e["event_type"] == "output"]
    return outputs[-1] if outputs else None


def render_chat_page(*, log_dir: Path, chat_client: ChatClient, model: str) -> None:
    run_ids = sorted((p.stem for p in Path(log_dir).glob("*.jsonl")), key=_timestamp, reverse=True)
    if not run_ids:
        st.info("No runs logged yet.")
        return
    run_id = st.sidebar.selectbox("Run", run_ids)
    call = _coordinator_call(log_dir, run_id)
    st.title(run_id)
    if call and call.get("call"):
        st.subheader(f"{call['call']} (conviction {call['conviction']})")
        st.markdown(call["thesis"])
    else:
        st.warning(f"No call for this run: {(call or {}).get('error', 'coordinator output missing')}")

    if st.session_state.get("history_run_id") != run_id:
        st.session_state["history"] = []
        st.session_state["history_run_id"] = run_id
    history = st.session_state["history"]
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if question := st.chat_input("Ask about this run"):
        with st.chat_message("user"):
            st.markdown(question)
        answer = answer_question(
            log_dir, run_id, question, chat_client=chat_client, model=model, history=history
        )
        with st.chat_message("assistant"):
            st.markdown(answer)
        history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])

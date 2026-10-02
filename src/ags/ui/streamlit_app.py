from pathlib import Path

import streamlit as st

from ags.llm.loop import ChatClient
from ags.logging.run_log import RunLog
from ags.ui.chat import answer_question
from ags.ui.format import to_markdown
from ags.ui.runs import format_run_id


def _timestamp(run_id: str) -> str:
    return run_id.rsplit("_", 1)[-1]


def coordinator_call(log_dir: Path, run_id: str) -> dict | None:
    outputs = [e["payload"] for e in RunLog(log_dir, run_id).read() if e["agent"] == "coordinator" and e["event_type"] == "output"]
    return outputs[-1] if outputs else None


_ANALYSTS = ("technical", "news", "weather", "supply_demand")


def _agent_outputs(log_dir: Path, run_id: str) -> dict[str, dict]:
    outputs = {}
    for event in RunLog(log_dir, run_id).read():
        if event["event_type"] == "output":
            outputs[event["agent"]] = event["payload"]
    return outputs


def render_call(call: dict) -> None:
    st.subheader(f"{call['call']} (conviction {call['conviction']})")
    st.markdown(call["thesis"])
    details = {k: v for k, v in call.items() if k not in ("call", "conviction", "thesis")}
    if details:
        st.markdown(to_markdown(details))


def render_chat_page(*, log_dir: Path, chat_client: ChatClient, model: str) -> None:
    run_ids = sorted((p.stem for p in Path(log_dir).glob("*.jsonl")), key=_timestamp, reverse=True)
    if not run_ids:
        st.info("No runs logged yet.")
        return
    selected = st.session_state.get("selected_run")
    run_id = st.sidebar.selectbox("Run", run_ids, format_func=format_run_id, index=run_ids.index(selected) if selected in run_ids else 0)
    call = coordinator_call(log_dir, run_id)
    st.title(format_run_id(run_id))
    if call and call.get("call"):
        render_call(call)
    else:
        st.warning(f"No call for this run: {(call or {}).get('error', 'coordinator output missing')}")

    outputs = _agent_outputs(log_dir, run_id)
    for agent in _ANALYSTS:
        if agent in outputs:
            with st.expander(agent.replace("_", " ").capitalize()):
                st.markdown(to_markdown(outputs[agent]))

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

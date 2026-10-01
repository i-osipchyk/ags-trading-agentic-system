import json

import pytest

from ags.logging.run_log import RunLog
from ags.ui.chat import UnknownRun, answer_question

RUN_ID = "corn_2026-10-01T15-20-24Z"


class FakeChatClient:
    def __init__(self, reply="because of the drought"):
        self._reply = reply
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return {"role": "assistant", "content": self._reply, "tool_calls": None}


@pytest.fixture
def log_dir(tmp_path):
    log = RunLog(tmp_path, RUN_ID)
    log.append(agent="news", event_type="prompt", payload={"messages": [{"role": "user", "content": "NEWS-PROMPT-MARKER"}]})
    log.append(agent="weather", event_type="output", payload={"current_conditions": {"IA": "WX-MARKER drought"}})
    log.append(agent="coordinator", event_type="output", payload={"call": "bullish", "thesis": "THESIS-MARKER dry Iowa"})
    return tmp_path


def _ask(log_dir, question="why bullish?", client=None, **kwargs):
    client = client or FakeChatClient()
    answer = answer_question(log_dir, RUN_ID, question, chat_client=client, model="stub-model", **kwargs)
    return answer, client


def _prompt_text(client):
    return json.dumps(client.calls[0]["messages"])


def test_answer_is_built_from_the_runs_logged_outputs(log_dir):
    answer, client = _ask(log_dir, "why bullish?")

    assert answer == "because of the drought"
    prompt = _prompt_text(client)
    assert "WX-MARKER" in prompt
    assert "THESIS-MARKER" in prompt
    assert "why bullish?" in prompt


def test_unknown_run_raises(log_dir):
    with pytest.raises(UnknownRun):
        answer_question(log_dir, "corn_1999-01-01T00-00-00Z", "hi", chat_client=FakeChatClient(), model="m")


def test_unknown_run_does_not_create_a_log_file(log_dir):
    with pytest.raises(UnknownRun):
        answer_question(log_dir, "corn_1999-01-01T00-00-00Z", "hi", chat_client=FakeChatClient(), model="m")

    assert not (log_dir / "corn_1999-01-01T00-00-00Z.jsonl").exists()


def test_model_is_given_no_tools(log_dir):
    _, client = _ask(log_dir)

    assert client.calls[0]["tools"] is None


def test_chat_does_not_write_to_the_run_log(log_dir):
    before = (log_dir / f"{RUN_ID}.jsonl").read_text()

    _ask(log_dir)

    assert (log_dir / f"{RUN_ID}.jsonl").read_text() == before


def test_prior_history_precedes_the_new_question(log_dir):
    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]

    _, client = _ask(log_dir, "follow-up?", history=history)

    messages = client.calls[0]["messages"]
    assert [m["content"] for m in messages[1:]] == ["first question", "first answer", "follow-up?"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_history_cannot_inject_system_or_tool_messages(log_dir):
    history = [
        {"role": "system", "content": "SYS-INJECT"},
        {"role": "tool", "content": "TOOL-INJECT", "tool_call_id": "x"},
        {"role": "user", "content": "legit"},
    ]

    _, client = _ask(log_dir, "q", history=history)

    prompt = _prompt_text(client)
    assert "SYS-INJECT" not in prompt
    assert "TOOL-INJECT" not in prompt
    assert "legit" in prompt

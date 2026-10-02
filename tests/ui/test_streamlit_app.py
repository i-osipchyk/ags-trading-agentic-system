import pytest
from streamlit.testing.v1 import AppTest

from ags.logging.run_log import RunLog

OLD = "corn_2026-09-01T15-00-00Z"
NEW = "wheat_2026-09-08T15-00-00Z"


class FakeChatClient:
    def __init__(self):
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return {"role": "assistant", "content": "an answer", "tool_calls": None}


def _page(log_dir, chat_client, model):
    from ags.ui.streamlit_app import render_chat_page

    render_chat_page(log_dir=log_dir, chat_client=chat_client, model=model)


@pytest.fixture
def chat_client():
    return FakeChatClient()


@pytest.fixture
def log_dir(tmp_path):
    RunLog(tmp_path, OLD).append(
        agent="coordinator", event_type="output", payload={"call": "bullish", "conviction": 4, "thesis": "OLD-THESIS"}
    )
    RunLog(tmp_path, NEW).append(
        agent="coordinator", event_type="output", payload={"call": "bearish", "conviction": 2, "thesis": "NEW-THESIS"}
    )
    return tmp_path


@pytest.fixture
def app(log_dir, chat_client):
    return AppTest.from_function(_page, kwargs={"log_dir": log_dir, "chat_client": chat_client, "model": "stub-model"}).run()


def _text(app):
    return " ".join(el.value for el in [*app.markdown, *app.title, *app.header, *app.subheader, *app.caption])


def test_runs_are_listed_newest_first_and_newest_is_selected(app):
    assert not app.exception
    assert list(app.sidebar.selectbox[0].options) == ["Wheat 2026-09-08 15:00 UTC", "Corn 2026-09-01 15:00 UTC"]
    assert app.sidebar.selectbox[0].value == NEW
    assert "NEW-THESIS" in _text(app)
    assert "bearish" in _text(app).lower()


def _ask(app, question):
    app.chat_input[0].set_value(question).run()
    return app


def _chat_text(app):
    return " ".join(m.markdown[0].value for m in app.chat_message)


def test_question_gets_an_answer_built_from_the_selected_run_only_with_no_tools(app, chat_client):
    app.sidebar.selectbox[0].select(OLD).run()

    _ask(app, "why bullish?")

    assert "an answer" in _chat_text(app)
    sent = str(chat_client.calls[0]["messages"])
    assert "OLD-THESIS" in sent
    assert "NEW-THESIS" not in sent
    assert "why bullish?" in sent
    assert chat_client.calls[0]["tools"] is None
    assert chat_client.calls[0]["model"] == "stub-model"


def test_follow_up_carries_the_earlier_exchange_and_transcript_persists(app, chat_client):
    _ask(app, "first question")
    _ask(app, "second question")

    roles = [m["role"] for m in chat_client.calls[1]["messages"]]
    contents = [m["content"] for m in chat_client.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert contents[1:] == ["first question", "an answer", "second question"]
    assert "first question" in _chat_text(app) and "second question" in _chat_text(app)


def test_switching_runs_clears_the_conversation(app, chat_client):
    _ask(app, "question about the newest run")

    app.sidebar.selectbox[0].select(OLD).run()
    _ask(app, "question about the old run")

    assert "newest run" not in _chat_text(app)
    sent = str(chat_client.calls[-1]["messages"])
    assert "question about the newest run" not in sent
    assert "NEW-THESIS" not in sent


def test_chatting_never_writes_to_the_run_log(app, log_dir):
    before = {p.name: p.read_text() for p in log_dir.glob("*.jsonl")}

    _ask(app, "why?")

    assert {p.name: p.read_text() for p in log_dir.glob("*.jsonl")} == before


def test_degraded_run_renders_without_crashing(tmp_path, chat_client):
    RunLog(tmp_path, OLD).append(
        agent="coordinator", event_type="output", payload={"call": None, "degraded": True, "error": "ValueError: boom"}
    )
    app = AppTest.from_function(_page, kwargs={"log_dir": tmp_path, "chat_client": chat_client, "model": "m"}).run()

    assert not app.exception
    assert "boom" in " ".join(el.value for el in [*app.markdown, *app.error, *app.warning, *app.caption])


def test_no_runs_shows_an_empty_state(tmp_path, chat_client):
    app = AppTest.from_function(_page, kwargs={"log_dir": tmp_path, "chat_client": chat_client, "model": "m"}).run()

    assert not app.exception
    assert "no runs" in " ".join(el.value for el in [*app.markdown, *app.info, *app.warning]).lower()

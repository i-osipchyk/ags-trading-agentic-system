from datetime import datetime, timezone

from ags.handlers.auditor import handle
from ags.handlers.runtime import Runtime
from ags.storage import LocalStorage


class ExplodingChatClient:
    def complete(self, messages, tools, *, model):
        raise AssertionError("nothing is due, the model must not be called")


def test_with_nothing_due_it_grades_nothing_and_reports_counts(tmp_path):
    runtime = Runtime(
        config=None, storage=LocalStorage(tmp_path), chat_client=ExplodingChatClient(), model="stub",
        market_client=None, now=lambda: datetime(2026, 6, 5, 22, 0, tzinfo=timezone.utc),
    )

    assert handle({}, runtime) == {"graded": 0, "failed": 0}

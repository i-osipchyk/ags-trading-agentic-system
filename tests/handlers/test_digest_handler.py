from datetime import datetime, timezone

from ags.handlers import digest
from ags.storage import LocalStorage


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_digest_handler_sends_the_composed_digest_to_telegram(tmp_path):
    telegram = FakeTelegram()

    result = digest.handle(
        {}, storage=LocalStorage(tmp_path), telegram=telegram,
        aws_cost=lambda: ("2026-06-09", 1.0), now=datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc),
    )

    assert result == {"sent": True}
    assert len(telegram.sent) == 1 and telegram.sent[0].startswith("<b>AGS digest · 2026-06-10</b>")

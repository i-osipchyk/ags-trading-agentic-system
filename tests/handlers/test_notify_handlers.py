import json
from datetime import datetime, timezone

from ags.handlers import digest, notify
from ags.notify.spend import LlmPrices
from ags.storage import LocalStorage


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_digest_handler_sends_the_composed_digest_to_telegram(tmp_path):
    telegram = FakeTelegram()

    result = digest.handle(
        {}, storage=LocalStorage(tmp_path), telegram=telegram, prices=LlmPrices(0.1, 1.0, 2.0),
        aws_cost=lambda: ("2026-06-09", 1.0), now=datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc),
    )

    assert result == {"sent": True}
    assert len(telegram.sent) == 1 and telegram.sent[0].startswith("<b>AGS digest · 2026-06-10</b>")


def _sns(alarm):
    return {"Records": [{"Sns": {"Message": json.dumps(alarm)}}]}


def test_cloudwatch_alarm_via_sns_becomes_a_telegram_alert():
    telegram = FakeTelegram()
    alarm = {"AlarmName": "ags-cycle-failed", "NewStateValue": "ALARM", "NewStateReason": "Execution failed: 1 >= 1"}

    notify.handle(_sns(alarm), telegram=telegram)

    assert len(telegram.sent) == 1
    assert "ags-cycle-failed" in telegram.sent[0] and "Execution failed" in telegram.sent[0]


def test_alarm_returning_to_ok_is_not_alerted():
    telegram = FakeTelegram()

    notify.handle(_sns({"AlarmName": "a", "NewStateValue": "OK", "NewStateReason": "r"}), telegram=telegram)

    assert telegram.sent == []

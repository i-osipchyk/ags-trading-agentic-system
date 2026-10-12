import pytest

from ags.notify.telegram import TelegramClient, TelegramError


class FakeHttp:
    def __init__(self, ok=True):
        self.ok = ok
        self.posts = []

    def __call__(self, url, payload):
        self.posts.append((url, payload))
        return {"ok": self.ok, "description": "Bad Request: chat not found"}


def test_message_is_posted_to_the_bot_in_html_mode():
    http = FakeHttp()

    TelegramClient("TOKEN", "42", http).send("<b>hi</b>")

    assert http.posts == [
        ("https://api.telegram.org/botTOKEN/sendMessage", {"chat_id": "42", "text": "<b>hi</b>", "parse_mode": "HTML"})
    ]


def test_long_message_is_split_on_line_boundaries_under_the_4096_limit():
    http = FakeHttp()
    lines = [f"line {i:04d} " + "x" * 90 for i in range(100)]  # ~10 kB

    TelegramClient("T", "1", http).send("\n".join(lines))

    chunks = [p["text"] for _, p in http.posts]
    assert len(chunks) == 3
    assert all(len(c) <= 4096 for c in chunks)
    assert "\n".join(chunks) == "\n".join(lines)


def test_telegram_rejection_raises():
    with pytest.raises(TelegramError, match="chat not found"):
        TelegramClient("T", "1", FakeHttp(ok=False)).send("hi")

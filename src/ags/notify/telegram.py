import requests

MAX_MESSAGE_LENGTH = 4096


class TelegramError(Exception):
    pass


def _post(url: str, payload: dict) -> dict:
    return requests.post(url, json=payload, timeout=15).json()


def _chunks(text: str) -> list[str]:
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_MESSAGE_LENGTH and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    chunks.append(current)
    return chunks


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http=_post) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._http = http

    def send(self, text: str) -> None:
        for chunk in _chunks(text):
            response = self._http(self._url, {"chat_id": self._chat_id, "text": chunk, "parse_mode": "HTML"})
            if not response.get("ok"):
                raise TelegramError(response.get("description", "unknown Telegram error"))

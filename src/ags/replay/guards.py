"""What makes a replay unable to touch the market or the official record."""

from ags.storage import Storage


class NoMarket:
    """A market client for replays: every call fails, so missing stored data is an error, never a fetch."""

    def __getattr__(self, name: str):
        def refuse(*args, **kwargs):
            raise RuntimeError(f"a replay reads stored market data only; {name} would have called cTrader")

        return refuse


class ReadOnly:
    """A storage view that refuses writes."""

    def __init__(self, inner: Storage) -> None:
        self._inner = inner

    def get(self, key: str) -> bytes | None:
        return self._inner.get(key)

    def list(self, prefix: str) -> list[str]:
        return self._inner.list(prefix)

    def put(self, key: str, data: bytes) -> None:
        raise PermissionError(f"a replay never writes official data: {key}")

    def put_if_absent(self, key: str, data: bytes) -> bool:
        raise PermissionError(f"a replay never writes official data: {key}")

import os
import tempfile
from pathlib import Path
from typing import Protocol

from ags.storage.s3 import S3Storage as S3Storage


class Storage(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, data: bytes) -> None: ...
    def put_if_absent(self, key: str, data: bytes) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStorage:
    """Keys are '/'-separated paths under `root`."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def get(self, key: str) -> bytes | None:
        path = self._root / key
        return path.read_bytes() if path.is_file() else None

    def put(self, key: str, data: bytes) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never sees a half-written file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise

    def put_if_absent(self, key: str, data: bytes) -> bool:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as f:
                f.write(data)
        except FileExistsError:
            return False
        return True

    def list(self, prefix: str) -> list[str]:
        directory, _, _ = prefix.rpartition("/")
        base = self._root / directory
        if not base.is_dir():
            return []
        keys = (p.relative_to(self._root).as_posix() for p in base.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix) and not Path(k).name.startswith(".tmp-"))


class Prefixed:
    """A view of another Storage with every key placed under `prefix/`."""

    def __init__(self, inner: Storage, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix.strip("/") + "/"

    def get(self, key: str) -> bytes | None:
        return self._inner.get(self._prefix + key)

    def put(self, key: str, data: bytes) -> None:
        self._inner.put(self._prefix + key, data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        return self._inner.put_if_absent(self._prefix + key, data)

    def list(self, prefix: str) -> list[str]:
        return [k.removeprefix(self._prefix) for k in self._inner.list(self._prefix + prefix)]


def as_storage(location: "Storage | Path | str") -> Storage:
    """Existing call sites pass a directory; treat it as a LocalStorage rooted there."""
    if isinstance(location, (str, Path)):
        return LocalStorage(location)
    return location

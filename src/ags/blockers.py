"""Provider errors that will fail every later cycle too until someone acts: no credit left, a key
that's rejected.

They're recorded where the provider is called (`llm/client.py` for DeepSeek,
`tools/sources/news.py` for Tavily), because further up they're caught and turned into degraded
outputs like any outage, and nothing raises. A cycle that hit one alerts at once and holds its
slot out of the paper book so it can be retried (ARCHITECTURE.md § Retrying a held cycle).

Per process: `entry.py` clears it at the start of every invocation. The four analysts record
from parallel threads, hence the lock.
"""

import threading

_lock = threading.Lock()
_seen: dict[str, str] = {}


def record(provider: str, detail: str) -> None:
    """The first blocking error per provider is kept; later ones are the same problem."""
    with _lock:
        _seen.setdefault(provider, detail[:300])


def seen() -> dict[str, str]:
    with _lock:
        return dict(_seen)


def clear() -> None:
    with _lock:
        _seen.clear()

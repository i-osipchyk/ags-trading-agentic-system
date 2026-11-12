import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ags.storage import Storage, as_storage

ET = ZoneInfo("America/New_York")

# Date-only releases are treated as published at their source's known time
# (ET); a source not listed here is assumed to publish at end of day, so a
# release is never visible earlier than it really was.
RELEASE_TIMES: dict[str, time] = {
    "usda_esmis": time(12, 0),
}

# A cycle whose scheduled time is at most this old is live; an older one is a replay and never fetches.
LIVE_CYCLE_MAX_AGE = timedelta(hours=1)

_END_OF_DAY = time.max
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H%M%S%fZ"


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, _END_OF_DAY, tzinfo=ET)


def _release_instant(source: str, stem: str) -> datetime:
    if "T" in stem:
        return datetime.strptime(stem, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    return datetime.combine(date.fromisoformat(stem), RELEASE_TIMES.get(source, _END_OF_DAY), tzinfo=ET)


def _stem(release: date | datetime) -> str:
    if isinstance(release, datetime):
        if release.tzinfo is None:
            raise ValueError("release timestamp must be timezone-aware")
        return release.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    return release.isoformat()


def release_instant(source: str, release: date | datetime) -> datetime:
    return _release_instant(source, _stem(release))


def is_released(source: str, release: date | datetime, as_of: datetime) -> bool:
    """True if a release (date-only or timestamped) was public at `as_of`."""
    _require_aware(as_of)
    return release_instant(source, release) <= as_of


def refresh_due(
    source: str, cached_release: date | datetime, *, as_of: datetime, now: datetime, max_age: timedelta
) -> bool:
    """Whether a live cycle should ask the source again instead of trusting its cached release.

    The cache returns the newest release stored by `as_of`, so on its own it never notices a newer one.
    A replayed or backtested `as_of` (not live) reads only what was stored; a live one asks again once the
    cached release is `max_age` old, the shortest gap a source's releases come at.
    """
    if now - as_of > LIVE_CYCLE_MAX_AGE:
        return False
    return as_of - release_instant(source, cached_release) >= max_age


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")


def read_pit(data_dir: Storage | Path, *, source: str, report: str, symbol: str, as_of: datetime) -> dict | None:
    _require_aware(as_of)
    store = as_storage(data_dir)
    prefix = f"{source}/{report}/{symbol}/"

    qualifying = [
        (instant, key)
        for key in store.list(prefix)
        if key.endswith(".json") and "/" not in key[len(prefix) :]
        if (instant := _release_instant(source, key[len(prefix) : -len(".json")])) <= as_of
    ]
    if not qualifying:
        return None

    _, latest_key = max(qualifying)
    return json.loads(store.get(latest_key))


def write_pit(
    data_dir: Storage | Path,
    *,
    source: str,
    report: str,
    symbol: str,
    release_date: date | datetime,
    content: dict,
    overwrite: bool = False,
) -> bool:
    """Store a release under its own instant. First write wins unless `overwrite` (a schema upgrade of the same release)."""
    key = f"{source}/{report}/{symbol}/{_stem(release_date)}.json"
    store = as_storage(data_dir)
    if overwrite:
        store.put(key, json.dumps(content).encode())
        return True
    return store.put_if_absent(key, json.dumps(content).encode())

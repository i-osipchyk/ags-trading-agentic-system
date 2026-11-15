import hashlib
import json
import statistics
from datetime import date, datetime
from pathlib import Path

from ags.tools.pit_store import ET, is_released

GROUNDED_SOURCE = "google_grounding"


def pit_filter(items: list[dict], *, as_of: datetime, source: str) -> list[dict]:
    """The filter `search_news` applies: an item with no date cannot be proven public, so it goes too."""
    return [i for i in items if i.get("date") and is_released(source, date.fromisoformat(i["date"]), as_of)]


def _ages(items: list[dict], as_of: datetime) -> dict:
    today = as_of.astimezone(ET).date()
    ages = [(today - date.fromisoformat(item["date"])).days for item in items]
    return {
        "kept": len(items),
        "median_age_days": statistics.median(ages) if ages else None,
        "oldest_age_days": max(ages) if ages else None,
    }


def _domain(item: dict) -> str:
    return (item["source"] or "").removeprefix("www.")


def compare(*, tavily: list[dict], grounded: list[dict], as_of: datetime) -> dict:
    """One query's Tavily results (as archived, already point-in-time filtered) next to what grounding returned."""
    kept = pit_filter(grounded, as_of=as_of, source=GROUNDED_SOURCE)
    return {
        "tavily": _ages(tavily, as_of),
        "grounded": {"returned": len(grounded), "leaked": len(grounded) - len(kept), **_ages(kept, as_of)},
        "overlap": {
            "shared_urls": len({i["url"] for i in tavily} & {i["url"] for i in kept}),
            "shared_domains": sorted({_domain(i) for i in tavily} & {_domain(i) for i in kept}),
        },
    }


def fetch_cached(cache_dir: Path, *, client, as_of: datetime, commodity: str, query: str) -> dict:
    """One billed grounded search per (as_of, commodity, query): a repeat reads the file instead of the API.

    Google cannot replay a past search, so the file is also the record of what it answered. A failed call
    raises and leaves nothing behind, so the next run asks again.
    """
    digest = hashlib.sha256(query.encode()).hexdigest()[:16]
    path = Path(cache_dir) / f"{as_of.astimezone(ET):%Y-%m-%d_%H%M}_{commodity}_{digest}.json"
    if path.exists():
        return {**json.loads(path.read_text()), "cached": True}
    grounded = client.search(query, published_before=as_of.astimezone(ET).date())
    record = {"as_of": as_of.isoformat(), "commodity": commodity, "query": query,
              "grounded": grounded, "raw": client.last_raw_response}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1))
    return {**record, "cached": False}

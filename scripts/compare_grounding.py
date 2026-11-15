"""CLI: replay archived Tavily news searches through Gemini + Google Search grounding and compare (read-only on data/).

Reads the news searches the cycles already ran from `data/tavily/search_news` (the Tavily side, as archived),
asks grounding for the same query with the same `as_of`, and keeps the raw answers in `grounding_compare/`
(git-ignored, never `data/`). An answer already there is read, not billed again, so a rerun or a bigger
`--limit` only pays for what is new. It is a dry run unless `--run` is given.

    uv run python scripts/compare_grounding.py                       # plan only: how many calls, which
    uv run python scripts/compare_grounding.py --run --limit 10      # an evenly spaced sample of 10
    uv run python scripts/compare_grounding.py --run --all --since 2026-11-09 --until 2026-11-09   # one day
    uv run python scripts/compare_grounding.py --run --all           # everything since --since

Live mode runs the archived queries again on BOTH providers with as_of = now (a live-performance comparison,
nothing to replay): `--live-as-of now` prints the as_of it used; pass that value back to reuse the cache.

Needs GEMINI_API_KEY in .env. Not a point-in-time replay: Google searches the web as it is today.
"""

import argparse
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from google.genai import errors

from ags import blockers
from ags.tools.grounding_compare import compare, fetch_cached, pit_filter
from ags.tools.pit_store import ET
from ags.tools.sources.grounded_news import GroundedNewsClient
from ags.tools.sources.news import SOURCE as TAVILY_SOURCE, TavilyNewsClient

_ROOT = Path(__file__).resolve().parents[1]  # repo root
_ARCHIVE = _ROOT / "data" / "tavily" / "search_news"
_CACHE = _ROOT / "grounding_compare"


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def load_archive(since: date, until: date | None = None) -> list[dict]:
    """Every archived search from `since` to `until` (inclusive), oldest first: commodity, query, as_of, the Tavily results."""
    rows = []
    for path in _ARCHIVE.glob("*/*.json"):
        record = json.loads(path.read_text())
        as_of = datetime.fromisoformat(record["as_of"])
        day = as_of.astimezone(ET).date()
        if day < since or (until and day > until):
            continue
        rows.append(
            {
                "commodity": path.parent.name.rsplit("_", 1)[0],
                "query": record["query"],
                "as_of": as_of,
                "tavily": record["results"],
            }
        )
    return sorted(rows, key=lambda r: (r["as_of"], r["commodity"], r["query"]))


def sample(rows: list[dict], limit: int) -> list[dict]:
    """`limit` rows spread evenly across the list, so a small sample still spans the days and commodities."""
    if limit >= len(rows):
        return rows
    return [rows[i * len(rows) // limit] for i in range(limit)]


def _fmt(value) -> str:
    return "-" if value is None else f"{value:g}"


def render(rows: list[dict]) -> str:
    lines = [
        "| as_of (ET) | commodity | query | tav kept | tav med/old age | gnd returned | leaked | gnd kept "
        "| gnd med/old age | shared urls | shared domains |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        c = r["report"]
        lines.append(
            f"| {r['as_of'].astimezone(ET):%m-%d %H:%M} | {r['commodity']} | {r['query']} "
            f"| {c['tavily']['kept']} | {_fmt(c['tavily']['median_age_days'])}/{_fmt(c['tavily']['oldest_age_days'])} "
            f"| {c['grounded']['returned']} | {c['grounded']['leaked']} | {c['grounded']['kept']} "
            f"| {_fmt(c['grounded']['median_age_days'])}/{_fmt(c['grounded']['oldest_age_days'])} "
            f"| {c['overlap']['shared_urls']} | {', '.join(c['overlap']['shared_domains']) or '-'} |"
        )
    returned = sum(r["report"]["grounded"]["returned"] for r in rows)
    leaked = sum(r["report"]["grounded"]["leaked"] for r in rows)
    usage = [r["usage"] for r in rows]
    lines += [
        "",
        f"**{len(rows)} queries ({sum(not r['cached'] for r in rows)} billed, {sum(r['cached'] for r in rows)} from "
        f"cache).** Grounding returned {returned} claims, {leaked} dated after `as_of` "
        f"({leaked / returned:.0%} leaked)." if returned else f"**{len(rows)} queries.** Grounding returned nothing.",
        f"Tavily kept {sum(r['report']['tavily']['kept'] for r in rows)}, grounding kept "
        f"{sum(r['report']['grounded']['kept'] for r in rows)}; "
        f"median of per-query median ages: Tavily "
        f"{_fmt(_median([r['report']['tavily']['median_age_days'] for r in rows]))} d, grounding "
        f"{_fmt(_median([r['report']['grounded']['median_age_days'] for r in rows]))} d.",
        f"Tokens: prompt {sum(u['prompt_tokens'] or 0 for u in usage)}, output "
        f"{sum(u['output_tokens'] or 0 for u in usage)}, thoughts {sum(u['thought_tokens'] or 0 for u in usage)}, "
        f"tool use {sum(u['tool_use_tokens'] or 0 for u in usage)}; "
        f"{sum(u['search_queries'] for u in usage)} Google searches. Price them from the current rates "
        f"(the per-search fee is not in the tokens).",
    ]
    return "\n".join(lines)


def _median(values: list):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare archived Tavily news searches with Google grounding.")
    parser.add_argument("--since", type=date.fromisoformat, default=_monday(date.today()),
                        help="archived searches from this date (default: Monday of this week)")
    parser.add_argument("--until", type=date.fromisoformat, help="archived searches up to this date, inclusive")
    parser.add_argument("--run", action="store_true", help="make the billed calls (default: plan only)")
    parser.add_argument("--limit", type=int, help="with --run: an evenly spaced sample of this many queries")
    parser.add_argument("--all", action="store_true", help="with --run: every query since --since")
    parser.add_argument("--live-as-of", help="'now' or an ISO timestamp: re-run the queries on both providers as of then")
    parser.add_argument("--out", type=Path, default=_CACHE, help="the cache of raw answers and the report")
    args = parser.parse_args(argv)

    rows = load_archive(args.since, args.until)
    if not args.run:
        cycles = len({r["as_of"] for r in rows})
        print(f"{len(rows)} archived searches over {cycles} cycles since {args.since}: that many billed "
              f"grounded calls with --all. Use --run --limit N for a sample.")
        return
    if not args.all and not args.limit:
        sys.exit("--run needs --limit N or --all, so a stray run cannot bill every query.")
    if not args.all:
        rows = sample(rows, args.limit)

    load_dotenv(_ROOT / ".env")
    client = GroundedNewsClient(api_key=os.environ["GEMINI_API_KEY"])
    out = args.out
    live = args.live_as_of is not None
    if live:
        as_of_live = (datetime.now(ET).replace(second=0, microsecond=0) if args.live_as_of == "now"
                      else datetime.fromisoformat(args.live_as_of))
        print(f"live as_of: {as_of_live.isoformat()}  (pass --live-as-of with this value to reuse the cache)")
        tavily_client = TavilyNewsClient(SimpleNamespace(tavily_api_key=os.environ["TAVILY_API_KEY"]))
        tavily_dropped = 0

    done = []
    for n, row in enumerate(rows, 1):
        try:
            if live:
                row = {**row, "as_of": as_of_live}
                raw_tavily = fetch_cached(out / "live_tavily", client=tavily_client, as_of=as_of_live,
                                          commodity=row["commodity"], query=row["query"])["grounded"]
                row["tavily"] = pit_filter(raw_tavily, as_of=as_of_live, source=TAVILY_SOURCE)
                tavily_dropped += len(raw_tavily) - len(row["tavily"])
                record = fetch_cached(out / "live_grounded", client=client, as_of=as_of_live,
                                      commodity=row["commodity"], query=row["query"])
            else:
                record = fetch_cached(out, client=client, as_of=row["as_of"], commodity=row["commodity"],
                                      query=row["query"])
        except Exception as exc:  # a provider error skips the query; a blocking one (recorded) stops the run
            print(f"[{n}/{len(rows)}] {row['query']!r}: {type(exc).__name__} {exc}"[:300], file=sys.stderr)
            if blockers.seen():
                print(f"stopping: blocking error {blockers.seen()}", file=sys.stderr)
                break
            continue
        done.append({**row, "report": compare(tavily=row["tavily"], grounded=record["grounded"], as_of=row["as_of"]),
                     "usage": record["raw"]["usage"], "cached": record["cached"]})
        print(f"[{n}/{len(rows)}] {'cached' if record['cached'] else 'billed'} {row['commodity']}: {row['query']}",
              flush=True)

    report = render(done) if done else "No queries completed."
    if live and done:
        report += f"\nLive: Tavily results dropped by the PIT filter (undated or not yet released): {tavily_dropped}."
    out.mkdir(parents=True, exist_ok=True)
    (out / ("live_report.md" if live else "report.md")).write_text(report + "\n")
    print(f"\n{report}\n\nraw answers and report: {out}")


if __name__ == "__main__":
    main()

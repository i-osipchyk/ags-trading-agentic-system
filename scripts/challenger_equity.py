"""CLI: equity curves of the shadow book on the live calls vs on the challenger's calls (10k and 100k accounts).

Replays both books from a flat account through the same guardrails and the same M5-priced fills, for the cycles since the
review window opened, under two policies for the commodity-cycles the challenger has no call for (see
ags/replay/challenger_books.py), and draws the curves. Local and read-only on the official record; it writes only under
replays/challenger-equity/. No model call and no order; the only network use is the M5 market-data fetch (cTrader).

Needs matplotlib, which is not a project dependency:
    uv run --with matplotlib python scripts/challenger_equity.py [--batch aligned-v1]
"""

import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from ags import scoring
from ags.config import load_env
from ags.portfolio.stage import COMMODITIES
from ags.replay.challenger_books import POLICIES, book_calls, challenger_output, max_drawdown, run_book
from ags.replay.guards import ReadOnly
from ags.replay.shadow_prices import MAX_STALE, SyntheticQuotes, fetch_m5, live_config, median_spreads
from ags.storage import LocalStorage, Prefixed

ROOT = Path(__file__).resolve().parents[1]
SIZES = {"10k": 10_000.0, "100k": 100_000.0}
LIVE, CHALLENGER = "#4c78a8", "#e8833a"
GAP_ENDS = "2026-11-02"       # coffee, sugar and cocoa have challenger calls from this date


def _cycles() -> dict[str, dict]:
    official = LocalStorage(ROOT)
    keys = sorted(k for k in official.list("portfolio/shadow/cycles/") if k.endswith(".json"))
    records = {k.rsplit("/", 1)[1].removesuffix(".json"): json.loads(official.get(k)) for k in keys}
    return {c: r for c, r in records.items() if datetime.fromisoformat(r["as_of"]).date() >= scoring.REVIEW_START}


def _simulate(args, records, out_dir) -> dict:
    official = LocalStorage(ROOT)
    instants = sorted({datetime.fromisoformat(r[k]) for r in records.values() for k in ("quoted_at", "exec_quoted_at") if r.get(k)})
    bars = fetch_m5(out_dir / "m5", instants[0] - MAX_STALE - timedelta(days=1), instants[-1] + timedelta(minutes=10))
    data = SyntheticQuotes(ReadOnly(Prefixed(official, "data")), bars, median_spreads(official), instants)
    logs = ROOT / "replays" / "analysts" / args.batch / "logs"
    base = live_config()
    results = {}
    for policy in POLICIES:
        calls = {"live": {}, "challenger": {}}
        for cycle_id, rec in records.items():
            stamp = cycle_id.removeprefix("cycle_")
            available = {c: challenger_output(logs, c, stamp) for c in COMMODITIES}
            live, challenger = book_calls(policy, rec["calls"], available)
            calls["live"][cycle_id], calls["challenger"][cycle_id] = live, challenger
        for size, equity in SIZES.items():
            for variant in ("live", "challenger"):
                store = LocalStorage(out_dir / f"{policy}-{variant}-{size}")
                results[(policy, variant, size)] = run_book(
                    records, calls[variant], store, config=replace(base, starting_equity=equity), data=data,
                )
                print(f"{policy:8} {variant:10} {size:>4}: closing equity {results[(policy, variant, size)][-1]['equity']:,.2f}")
    return results


def _plot(results, records, out_dir) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    official = [(datetime.fromisoformat(r["as_of"]), r["equity"]) for _, r in sorted(records.items())]
    gap_end = datetime.fromisoformat(f"{GAP_ENDS}T00:00:00+00:00")
    titles = {
        "fallback": "Same universe: the challenger book uses the live call where the challenger made none",
        "matched": "Matched universe: neither book trades a commodity-cycle the challenger made no call for",
    }
    for policy in POLICIES:
        fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]})
        for col, size in enumerate(SIZES):
            live, chal = results[(policy, "live", size)], results[(policy, "challenger", size)]
            x = [datetime.fromisoformat(p["as_of"]) for p in live]
            start = SIZES[size]
            top, bottom = axes[0][col], axes[1][col]
            top.plot(x, [p["equity"] for p in live], color=LIVE, lw=2, label="live calls")
            top.plot(x, [p["equity"] for p in chal], color=CHALLENGER, lw=2, label="challenger calls")
            if size == "10k" and policy == "fallback":      # the recorded book trades all seven, as `fallback` does
                top.plot(*zip(*official), color="#999999", lw=1.2, ls=":", label="recorded shadow book (real quotes)")
            top.axhline(start, color="#bbbbbb", lw=0.8)
            gap = [100 * (c["equity"] - l["equity"]) / start for c, l in zip(chal, live)]
            bottom.plot(x, gap, color=CHALLENGER, lw=1.6)
            bottom.axhline(0, color="#999999", lw=0.8)
            for ax in (top, bottom):
                ax.axvline(gap_end, color="#777777", ls="--", lw=1)
                ax.grid(alpha=0.25)
            top.text(gap_end, top.get_ylim()[1], " coffee, sugar, cocoa\n challenger calls start", va="top", ha="left", fontsize=8, color="#555555")
            ll, cc = live[-1]["equity"], chal[-1]["equity"]
            dl, dc = max_drawdown([p["equity"] for p in live])[1], max_drawdown([p["equity"] for p in chal])[1]
            top.set_title(f"{size} account   live {100*(ll/start-1):+.1f}% (max dd {100*dl:.1f}%)   "
                          f"challenger {100*(cc/start-1):+.1f}% (max dd {100*dc:.1f}%)", fontsize=10)
            top.set_ylabel("shadow-book equity, USD")
            bottom.set_ylabel("challenger − live, % of start")
            bottom.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            top.legend(loc="upper left", fontsize=8)
        fig.suptitle(titles[policy], fontsize=11)
        fig.tight_layout()
        fig.savefig(out_dir / f"equity-{policy}.png", dpi=130)
        plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Equity curves: live calls vs the challenger's, 10k and 100k.")
    parser.add_argument("--batch", default="aligned-v1")
    args = parser.parse_args(argv)
    load_env(ROOT / ".env")
    out_dir = ROOT / "replays" / "challenger-equity"
    records = _cycles()
    print(f"{len(records)} cycles since {scoring.REVIEW_START}")
    results = _simulate(args, records, out_dir)
    (out_dir / "series.json").write_text(json.dumps({"|".join(k): v for k, v in results.items()}, indent=1))
    _plot(results, records, out_dir)
    print(f"charts in {out_dir}")


if __name__ == "__main__":
    main()

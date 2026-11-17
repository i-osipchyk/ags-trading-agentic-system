"""CLI: replay only the shadow book over the logged calls, from a flat account of a chosen size.

Local and read-only on the official record: the calls, quotes, specs and bars come from the local
mirror (`scripts/sync_local.sh`), and everything written goes to replays/{replay_id}/. No model
call, no order, no AWS write; the only network use is the M5 market-data fetch below. The manager stage is stubbed to hold everything, so the manager book
in the replay is meaningless; read `replays/{replay_id}/portfolio/shadow/cycles/`.

Fills are priced from M5 bars, not the stored quotes (some were captured with another symbol's tick).
For each cycle's decision and execution instants the quote is the open of the M5 bar containing that
instant (so nothing after it is used), as the bid, plus the symbol's median spread from its good stored
quotes. The bars are fetched once from cTrader (market data only, no orders) and cached under
replays/{replay_id}/m5/.

Sizers (`--sizer`): `conviction` is the deployed rule; `flat_hold` is long/short one lot on a
bullish/bearish call and holds on neutral; `flat_exit` is the same but a neutral call closes the position.
`risk_hold` / `risk_exit` are those two with the position sized to the full risk budget (1 ATR = SHADOW_RISK_BUDGET
of equity, as conviction 5), so they differ from the deployed rule only in ignoring conviction.
The flat sizers are a backtest only: they patch the stage in this process, nothing in src/ changes.

`--trend short|medium` replaces every call with the technical analyst's logged trend state of that horizon
(up = bullish, down = bearish, otherwise neutral; conviction 5, so the conviction sizer takes the full
risk budget). It is the trend-only baseline through the same costs and guardrails.

`--confirm N` holds each commodity's position direction until N consecutive opposite calls: the first
directional call opens, a neutral or failed call neither flips nor counts, and an opposite call that is not
confirmed yet is passed on as neutral (the guardrails then hold the leg). Applied to whichever calls are
replayed (the logged ones, or `--trend`); the calls themselves are not changed on the official record.

Usage: uv run python scripts/run_shadow_replay.py [--equity 100000] [--replay-id shadow-100k]
                                                 [--sizer conviction|flat_hold|flat_exit|risk_hold|risk_exit]
"""

import argparse
import json
import statistics
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.config import Config, load_env
from ags.portfolio.stage import COMMODITIES, PortfolioConfig, run_portfolio
from ags.replay.guards import NoMarket, ReadOnly
from ags.storage import LocalStorage, Prefixed
from ags.tools.pit_store import _stem
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, CTraderPricesClient

_BAR = timedelta(minutes=5)
_MAX_STALE = timedelta(days=4)  # longer than a weekend/holiday closure: beyond it the data has a gap, not a closed market
_CHUNK = timedelta(days=2)

ROOT = Path(__file__).parent.parent


def _hold_all_manager(*args, **kwargs) -> dict:
    return {"proposal": None}


def _fetch_m5(cache: Path, start: datetime, end: datetime) -> dict[str, list[dict]]:
    """M5 bars per commodity covering [start, end], from the cache, fetching what is missing once."""
    bars: dict[str, list[dict]] = {}
    client = None
    try:
        for c in COMMODITIES:
            path = cache / f"{c}.json"
            out: dict[str, dict] = {}
            t = start
            if path.is_file():
                cached = json.loads(path.read_text())
                if datetime.fromisoformat(cached[-1]["timestamp"]) >= end - timedelta(hours=12):
                    bars[c] = cached
                    continue
                out = {b["timestamp"]: b for b in cached}  # top up the cache from its last bar
                t = datetime.fromisoformat(cached[-1]["timestamp"])
            client = client or CTraderPricesClient(Config.from_env())
            while t < end:
                for bar in client.get_bars(CTRADER_SYMBOL_NAMES[c], "M5", t, min(t + _CHUNK, end)):
                    out[bar["timestamp"]] = bar
                t += _CHUNK
            bars[c] = [out[k] for k in sorted(out)]
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(bars[c]))
            print(f"{len(bars[c])} M5 bars for {c}")
    finally:
        if client:
            client.close()
    return bars


def _median_spreads(official: LocalStorage) -> dict[str, float]:
    """Each symbol's median ask-bid over stored quotes carrying its own symbol id (per its latest specs)."""
    out = {}
    for c in COMMODITIES:
        specs = sorted(official.list(f"data/ctrader/symbol_specs/{c}/"))
        sid = json.loads(official.get(specs[-1]))["specs"]["symbol_id"]
        quotes = [json.loads(official.get(k)) for k in official.list(f"data/ctrader/quotes/{c}/")]
        spreads = [q["ask"] - q["bid"] for q in quotes if q["symbol_id"] == sid and q["ask"] > q["bid"]]
        if not spreads:
            raise RuntimeError(f"{c}: no stored quote with symbol id {sid} to take a spread from")
        out[c] = statistics.median(spreads)
    return out


class _SyntheticQuotes:
    """The official data store with `ctrader/quotes` replaced by M5-priced quotes at the given instants."""

    def __init__(self, inner, bars: dict[str, list[dict]], spreads: dict[str, float], instants: list[datetime]):
        self._inner = inner
        self._quotes: dict[str, bytes] = {}
        self.closed_market: list[tuple[str, str]] = []
        stamps = {c: [datetime.fromisoformat(b["timestamp"]) for b in bs] for c, bs in bars.items()}
        for c in COMMODITIES:
            for t in instants:
                i = bisect_right(stamps[c], t) - 1
                if i < 0 or t - stamps[c][i] > _MAX_STALE:
                    raise RuntimeError(f"{c}: no M5 bar within {_MAX_STALE} before {t.isoformat()}")
                if t - stamps[c][i] < _BAR:
                    bid = bars[c][i]["open"]  # the open of the bar the instant falls in: nothing after it
                else:
                    bid = bars[c][i]["close"]  # market closed: the last tick, as a live quote would be
                    self.closed_market.append((c, t.isoformat()))
                quote = {"symbol_id": None, "bid": bid, "ask": bid + spreads[c], "timestamp": t.isoformat()}
                self._quotes[f"ctrader/quotes/{c}/{_stem(t)}.json"] = json.dumps(quote).encode()

    def get(self, key: str):
        return self._quotes.get(key) if key.startswith("ctrader/quotes/") else self._inner.get(key)

    def list(self, prefix: str) -> list[str]:
        if prefix.startswith("ctrader/quotes/"):
            return sorted(k for k in self._quotes if k.startswith(prefix))
        return self._inner.list(prefix)

    def put(self, key, data):
        raise PermissionError(key)

    put_if_absent = put


def _flat_sizer(exit_on_neutral: bool, risk_matched: bool = False):
    """Patch the stage with a one-lot sizer, and (for exit_on_neutral) let a neutral call close the leg.

    Guardrail 2 freezes a neutral leg, but a bullish call may end flat, so a neutral call reaches the
    guardrails as bullish when the target is flat. Only the shadow book's guardrail call is affected
    because the manager stub holds everything (a held leg is not a flat target).
    """
    from ags.portfolio import stage

    def proposal(calls, book_state, risk, risk_budget):
        legs = []
        for symbol, held in book_state["lots"].items():
            call = calls.get(symbol)
            if call is None or "error" in risk[symbol] or (call["call"] == "neutral" and not exit_on_neutral):
                delta = 0.0
            else:
                size = risk_budget * book_state["equity"] / risk[symbol]["atr_usd_per_lot"] if risk_matched else 1.0
                target = {"bullish": size, "bearish": -size, "neutral": 0.0}[call["call"]]
                delta = target - held
            legs.append({"symbol": symbol, "delta_lots": delta, "rationale": "flat sizer"})
        return {"legs": legs, "portfolio_note": "flat sizer"}

    real_guardrails = stage.apply_guardrails

    def guardrails(proposal_, book_state, calls, *args, **kwargs):
        if exit_on_neutral and proposal_ and proposal_.get("portfolio_note") == "flat sizer":
            calls = {s: ({**c, "call": "bullish"} if c and c["call"] == "neutral" else c) for s, c in calls.items()}
        return real_guardrails(proposal_, book_state, calls, *args, **kwargs)

    stage.shadow_proposal = proposal
    stage.apply_guardrails = guardrails


def _trend_calls(official_calls: dict, stamp: str, horizon: str) -> dict:
    """The cycle's calls rebuilt from the logged technical trend state; failed commodities stay failed."""
    out = {}
    for c, call in official_calls.items():
        trend = None
        if call is not None:
            for line in (ROOT / "logs" / f"{c}_{stamp}.jsonl").read_text().splitlines():
                e = json.loads(line)
                if e["agent"] == "technical" and e["event_type"] == "output":
                    trend = e["payload"]["trend"][horizon]["state"]
        out[c] = None if call is None or trend is None else {
            **call, "call": {"up": "bullish", "down": "bearish"}.get(trend, "neutral"), "conviction": 5,
        }
    return out


def _confirmed(records: dict[str, dict], n: int) -> None:
    """Rewrite each cycle's calls so a direction flips only after `n` consecutive opposite calls."""
    held: dict[str, str] = {}
    streak: dict[str, int] = {}
    for cycle_id in sorted(records):
        calls = records[cycle_id]["calls"]
        for c, call in calls.items():
            if call is None or call["call"] == "neutral":
                streak[c] = 0
                continue
            direction = call["call"]
            if c not in held:
                held[c], streak[c] = direction, 0
            elif direction == held[c]:
                streak[c] = 0
            else:
                streak[c] = streak.get(c, 0) + 1
                if streak[c] >= n:
                    held[c], streak[c] = direction, 0
                else:
                    calls[c] = {**call, "call": "neutral"}


def _live_config() -> PortfolioConfig:
    """The deployed portfolio Lambda's limits; the documented defaults when AWS isn't reachable."""
    try:
        import boto3

        env = boto3.client("lambda").get_function_configuration(FunctionName="ags-live-portfolio")
        return PortfolioConfig.from_mapping(env["Environment"]["Variables"])
    except Exception as exc:
        print(f"using default limits (could not read the Lambda's: {type(exc).__name__})")
        return PortfolioConfig.from_mapping({})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replay the shadow book from a flat account.")
    parser.add_argument("--equity", type=float, default=100_000)
    parser.add_argument("--replay-id", default="shadow-100k")
    parser.add_argument("--confirm", type=int, default=0, help="flip a position only after N consecutive opposite calls")
    parser.add_argument("--trend", choices=("short", "medium"))
    parser.add_argument("--sizer", choices=("conviction", "flat_hold", "flat_exit", "risk_hold", "risk_exit"), default="conviction")
    args = parser.parse_args(argv)
    load_env(ROOT / ".env")
    if args.sizer != "conviction":
        _flat_sizer(exit_on_neutral=args.sizer.endswith("_exit"), risk_matched=args.sizer.startswith("risk_"))

    official = LocalStorage(ROOT)
    out_dir = ROOT / "replays" / args.replay_id
    replay = LocalStorage(out_dir)
    config = replace(_live_config(), starting_equity=args.equity)
    cycles = sorted(
        k.rsplit("/", 1)[1].removesuffix(".json")
        for k in official.list("portfolio/shadow/cycles/") if k.endswith(".json")
    )
    print(f"{len(cycles)} cycles, starting equity {args.equity:,.0f}")
    records = {c: json.loads(official.get(f"portfolio/shadow/cycles/{c}.json")) for c in cycles}
    if args.trend:
        for cycle_id, rec in records.items():
            rec["calls"] = _trend_calls(rec["calls"], cycle_id.removeprefix("cycle_"), args.trend)
    if args.confirm:
        _confirmed(records, args.confirm)
    instants = sorted({
        datetime.fromisoformat(r[k]) for r in records.values() for k in ("quoted_at", "exec_quoted_at") if r.get(k)
    })
    bars = _fetch_m5(out_dir / "m5", instants[0] - _MAX_STALE - timedelta(days=1), instants[-1] + timedelta(minutes=10))
    data = _SyntheticQuotes(
        ReadOnly(Prefixed(official, "data")), bars, _median_spreads(official), instants
    )
    print(f"{len(data.closed_market)} of {len(instants) * len(COMMODITIES)} quotes priced at a closed market's last bar close")
    # A day ahead, so a cycle from today still reads the stored specs instead of treating itself as live.
    now = datetime.now(timezone.utc) + timedelta(days=1)
    for cycle_id in cycles:
        if replay.get(f"portfolio/shadow/cycles/{cycle_id}.json"):
            continue  # resume
        rec = records[cycle_id]
        exec_at = rec.get("exec_quoted_at")
        run_portfolio(
            cycle_id, rec["calls"], replay, as_of=datetime.fromisoformat(rec["as_of"]), chat_client=None,
            now=now, model="none", market_client=NoMarket(), config=config, data=data,
            quoted_at=datetime.fromisoformat(rec["quoted_at"]),
            execution_quoted_at=datetime.fromisoformat(exec_at) if exec_at else None,
            manager=_hold_all_manager, hold_on_manager_error=False,
        )
    last = sorted(replay.list("portfolio/shadow/cycles/"))[-1]
    final = json.loads(replay.get(last))
    print(f"closing equity {final['equity']:,.2f}; lots {final['closing_lots']}")
    print(f"replay in {out_dir}")


if __name__ == "__main__":
    main()

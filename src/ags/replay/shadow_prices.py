"""Price a replayed shadow book from M5 bars instead of the stored quotes (shared by the shadow replays).

Some stored quotes were captured with another symbol's tick, so a replay prices each instant from the symbol's own M5
bars: the open of the bar containing the instant (nothing after it is used) as the bid, plus the symbol's median spread
from its good stored quotes. The bars come from cTrader (market data only, no orders), cached once per commodity.
"""

import json
import statistics
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

from ags.config import Config
from ags.portfolio.stage import COMMODITIES, PortfolioConfig
from ags.storage import LocalStorage
from ags.tools.pit_store import _stem
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, CTraderPricesClient

_BAR = timedelta(minutes=5)
MAX_STALE = timedelta(days=4)  # longer than a weekend/holiday closure: beyond it the data has a gap, not a closed market
_CHUNK = timedelta(days=2)


def hold_all_manager(*args, **kwargs) -> dict:
    return {"proposal": None}


def fetch_m5(cache: Path, start: datetime, end: datetime) -> dict[str, list[dict]]:
    """M5 bars per commodity covering [start, end], from the cache, fetching what is missing once."""
    bars: dict[str, list[dict]] = {}
    client = None
    try:
        for c in COMMODITIES:
            path = cache / f"{c}.json"
            if path.is_file():
                bars[c] = json.loads(path.read_text())
                continue
            client = client or CTraderPricesClient(Config.from_env())
            out: dict[str, dict] = {}
            t = start
            while t < end:
                for bar in client.get_bars(CTRADER_SYMBOL_NAMES[c], "M5", t, min(t + _CHUNK, end)):
                    out[bar["timestamp"]] = bar
                t += _CHUNK
            bars[c] = [out[k] for k in sorted(out)]
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(bars[c]))
            print(f"fetched {len(bars[c])} M5 bars for {c}")
    finally:
        if client:
            client.close()
    return bars


def median_spreads(official: LocalStorage) -> dict[str, float]:
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


class SyntheticQuotes:
    """The official data store with `ctrader/quotes` replaced by M5-priced quotes at the given instants."""

    def __init__(self, inner, bars: dict[str, list[dict]], spreads: dict[str, float], instants: list[datetime]):
        self._inner = inner
        self._quotes: dict[str, bytes] = {}
        self.closed_market: list[tuple[str, str]] = []
        stamps = {c: [datetime.fromisoformat(b["timestamp"]) for b in bs] for c, bs in bars.items()}
        for c in COMMODITIES:
            for t in instants:
                i = bisect_right(stamps[c], t) - 1
                if i < 0 or t - stamps[c][i] > MAX_STALE:
                    raise RuntimeError(f"{c}: no M5 bar within {MAX_STALE} before {t.isoformat()}")
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


def live_config() -> PortfolioConfig:
    """The deployed portfolio Lambda's limits; the documented defaults when AWS isn't reachable."""
    try:
        import boto3

        env = boto3.client("lambda").get_function_configuration(FunctionName="ags-live-portfolio")
        return PortfolioConfig.from_mapping(env["Environment"]["Variables"])
    except Exception as exc:
        print(f"using default limits (could not read the Lambda's: {type(exc).__name__})")
        return PortfolioConfig.from_mapping({})



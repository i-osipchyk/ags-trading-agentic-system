"""Scores the coordinator's logged calls against mechanical baselines, from the local mirror only.

Read-only and model-free: it reads `logs/` (the calls) and `data/ctrader/daily_bars/` (closes) and never
fetches, grades or writes anything. Each call is entered at its own intraday price (`price_at_call`) and
exited at the `horizon_days`-th close from its own day on (the call day's close is the first, as in the
auditor's intraday path), so a call is scored only once that close is in the mirror: a stale mirror silently
scores fewer calls, which is why `price_data_through` is shown next to every result. Baselines are scored on exactly the same rows:

- `always_long` / `always_short`
- `trend`: the medium-trend state at the call, as the auditor's `trend_benchmark` takes it
- `coordinator`: bullish +1, bearish -1, neutral 0

"Market-neutral" subtracts, from each call's return, the mean return of every call in the same cycle (the
basket), which removes the drift all seven commodities share. Each commodity counts once per ISO week of the
call: calls on one commodity in the same week share most of their price path, so they split a weight of 1 between
them, and a week is worth a full 1 only when it has the scheduled `EXPECTED_CALLS_PER_WEEK` calls (2 a day, 5
days): a thinner week (a one-off dev run, a holiday, the week in progress) is worth its share. The effective
count is then the number of commodity-weeks, and effective weeks that divided by the seven commodities, i.e. the
calendar weeks of evidence. Intervals are bootstrapped over the same (commodity, ISO
week) clusters. (`scorecard.effective_weights`, which counts every call within a week on either side as an
overlap, halves that and is not used here.)
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ags.agents.auditor import _triggered_at
from ags.tools.pit_store import ET
from ags.tools.sources.prices import _session_date
from ags.tools.trend import get_trend_state

STRATEGIES = ("coordinator", "always_long", "always_short", "trend")
COMMODITIES = ("corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa")
HORIZONS = {"1 day": 1, "1 week": 5}
# The review window's start, confirmed by the owner (ROADMAP.md Step 0, item 1): the first day of the scheduled
# cycles; the earlier runs were dev runs. Later runs that predate the fixes in ROADMAP.md § 1 are still in the
# window, and show as "unversioned" in the by-version cut.
REVIEW_START = date(2026, 10, 13)
EXPECTED_CALLS_PER_WEEK = 10
VERDICT_HORIZON = 5

# A cycle needs this many scored calls for its basket mean to stand for "the market".
MIN_BASKET = 4
_BOOTSTRAP_DRAWS = 2000
_SEED = 7


@dataclass(frozen=True)
class PassBar:
    """The bar the coordinator must clear, fixed before the experiments so a result can't move it.

    Proposed from ROADMAP.md § Open decisions; the owner confirms or changes it. The verdict is "pass" when,
    at the 1-week horizon and market-neutral, the coordinator's weighted mean return beats the trend rule's
    with a bootstrap interval of the difference that excludes zero, over at least `min_effective_weeks`
    effective weeks (effective calls / commodities) of which `min_non_falling_weeks` saw the basket not fall.
    """

    min_effective_weeks: float = 8.0
    min_non_falling_weeks: int = 2
    confidence: float = 0.90


PASS_BAR = PassBar()


def _version_label(events: list[dict]) -> str:
    for event in events:
        if event["agent"] == "pipeline" and event["event_type"] == "version":
            payload = event["payload"]
            return f"{payload['code_version'][:7]}/{payload['prompt_hashes']['all'][:6]}"
    return "unversioned"


def _logged_call(path: Path) -> dict | None:
    events = [json.loads(line) for line in path.read_text().splitlines() if line]
    outputs = [e["payload"] for e in events if e["agent"] == "coordinator" and e["event_type"] == "output"]
    call = outputs[-1] if outputs else None
    if not call or call.get("call") not in ("bullish", "bearish", "neutral"):
        return None
    price = (call.get("price_at_call") or {}).get("price")
    if price is None:
        return None
    return {"call": call["call"], "conviction": call.get("conviction"), "entry": float(price),
            "version": _version_label(events)}


def load_closes(data_dir: Path, commodity: str) -> pd.Series:
    folder = Path(data_dir) / "ctrader" / "daily_bars" / commodity
    # A file is named by cTrader's stamp, the evening before the session its bar covers: the session date is
    # `_session_date(stamp)`, the same shift `get_prices` applies, and the one calls and closes are matched on.
    records = {pd.Timestamp(_session_date(date.fromisoformat(p.stem))): json.loads(p.read_text()) for p in folder.glob("*.json")}
    # A day the market was closed is stored as {"no_trading": true}, with no bar.
    return pd.Series({day: r["close"] for day, r in records.items() if "close" in r}, dtype=float).sort_index()


def price_data_through(data_dir: Path) -> date | None:
    """The newest daily bar in the mirror across the commodities, the date scoring can reach."""
    newest = [closes.index[-1].date() for c in COMMODITIES if not (closes := load_closes(data_dir, c)).empty]
    return min(newest) if newest else None


def _exit_close(closes: pd.Series, call_day: pd.Timestamp, horizon_days: int) -> float | None:
    after = closes[closes.index >= call_day]
    return float(after.iloc[horizon_days - 1]) if len(after) >= horizon_days else None


def _trend_position(closes: pd.Series, call_day: pd.Timestamp) -> int | None:
    before = closes[closes.index < call_day]
    if before.empty:
        return None
    return {"up": 1, "down": -1, "sideways": 0}[get_trend_state(before)["medium"]["state"]]


def build_rows(
    log_dir: Path, data_dir: Path, *, horizon_days: int, since: date | None = REVIEW_START
) -> pd.DataFrame:
    """One row per logged call old enough to have an exit close, with each strategy's position and return."""
    closes = {c: load_closes(data_dir, c) for c in COMMODITIES}
    rows = []
    for path in sorted(Path(log_dir).glob("*.jsonl")):
        triggered = _triggered_at(path.stem)
        commodity = path.stem.rsplit("_", 1)[0]
        if triggered is None or commodity not in closes or closes[commodity].empty:
            continue
        day = triggered.astimezone(ET).date()
        if since is not None and day < since:
            continue
        logged = _logged_call(path)
        if logged is None:
            continue
        exit_price = _exit_close(closes[commodity], pd.Timestamp(day), horizon_days)
        trend = _trend_position(closes[commodity], pd.Timestamp(day))
        if exit_price is None or trend is None:
            continue
        iso = day.isocalendar()
        rows.append({
            "run_id": path.stem, "commodity": commodity, "triggered_at": triggered,
            "week": f"{iso.year}-W{iso.week:02d}", "call": logged["call"], "conviction": logged["conviction"],
            "version": logged["version"],
            "ret": exit_price / logged["entry"] - 1,
            "pos_coordinator": {"bullish": 1, "bearish": -1, "neutral": 0}[logged["call"]],
            "pos_always_long": 1, "pos_always_short": -1, "pos_trend": trend,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values("triggered_at", kind="stable").reset_index(drop=True)
    in_week = frame.groupby(["commodity", "week"])["ret"].transform("size")
    frame["weight"] = 1 / in_week.clip(lower=EXPECTED_CALLS_PER_WEEK)
    # The basket mean per cycle: calls in one cycle share a trigger timestamp.
    cycle = frame.groupby("triggered_at")["ret"]
    frame["basket"] = cycle.transform("mean").where(cycle.transform("size") >= MIN_BASKET)
    for strategy in STRATEGIES:
        frame[f"raw_{strategy}"] = frame[f"pos_{strategy}"] * frame["ret"]
        frame[f"neutral_{strategy}"] = frame[f"pos_{strategy}"] * (frame["ret"] - frame["basket"])
    return frame


def _kind(neutral: bool) -> str:
    return "neutral" if neutral else "raw"


def _usable(frame: pd.DataFrame, neutral: bool) -> pd.DataFrame:
    return frame.dropna(subset=["basket"]) if neutral else frame


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    return float((values * weights).sum() / weights.sum()) if weights.sum() else float("nan")


def bootstrap_interval(
    frame: pd.DataFrame, values: pd.Series, *, confidence: float = PassBar.confidence
) -> tuple[float, float]:
    """The weighted mean's interval, resampling whole (commodity, week) clusters of calls."""
    if frame.empty:
        return float("nan"), float("nan")
    keys = frame["commodity"] + frame["week"]
    weights = frame["weight"]
    sums = pd.DataFrame({"w": weights, "wx": weights * values, "k": keys}).groupby("k").sum()
    rng = np.random.default_rng(_SEED)
    picks = rng.integers(0, len(sums), (_BOOTSTRAP_DRAWS, len(sums)))
    means = sums["wx"].to_numpy()[picks].sum(axis=1) / sums["w"].to_numpy()[picks].sum(axis=1)
    tail = (1 - confidence) / 2 * 100
    return float(np.percentile(means, tail)), float(np.percentile(means, 100 - tail))


def summarize(frame: pd.DataFrame, *, neutral: bool, strategies: tuple[str, ...] = STRATEGIES) -> pd.DataFrame:
    """One row per strategy: calls scored, effective count, weighted hit rate and mean return with its interval.

    The hit rate counts only calls that took a side; the mean return counts all of them (a neutral call earns 0),
    so abstaining is not free of cost.
    """
    columns = ["strategy", "calls", "effective", "directional", "hit_rate", "mean_return", "ci_low", "ci_high"]
    use = _usable(frame, neutral)
    if use.empty:
        return pd.DataFrame(columns=columns)
    out = []
    for strategy in strategies:
        returns = use[f"{_kind(neutral)}_{strategy}"]
        taking_a_side = use[use[f"pos_{strategy}"] != 0]
        hits = (taking_a_side[f"{_kind(neutral)}_{strategy}"] > 0).astype(float)
        low, high = bootstrap_interval(use, returns)
        out.append({
            "strategy": strategy, "calls": len(use), "effective": float(use["weight"].sum()),
            "directional": len(taking_a_side),
            "hit_rate": _weighted_mean(hits, taking_a_side["weight"]) if len(taking_a_side) else float("nan"),
            "mean_return": _weighted_mean(returns, use["weight"]), "ci_low": low, "ci_high": high,
        })
    return pd.DataFrame(out, columns=columns)


def breakdown(frame: pd.DataFrame, by: str, *, neutral: bool) -> pd.DataFrame:
    """Weighted mean return of every strategy (and the coordinator's hit rate) within each value of column `by`."""
    use = _usable(frame, neutral)
    rows = []
    for key, group in use.groupby(by):
        directional = group[group["pos_coordinator"] != 0]
        hits = (directional[f"{_kind(neutral)}_coordinator"] > 0).astype(float)
        rows.append({
            by: key, "calls": len(group), "effective": float(group["weight"].sum()),
            "coordinator_hit_rate": _weighted_mean(hits, directional["weight"]) if len(directional) else float("nan"),
            **{s: _weighted_mean(group[f"{_kind(neutral)}_{s}"], group["weight"]) for s in STRATEGIES},
        })
    return pd.DataFrame(rows)


def by_direction(frame: pd.DataFrame, *, neutral: bool) -> pd.DataFrame:
    """The coordinator's bullish and bearish calls separately: how many, how often right, mean return."""
    use = _usable(frame, neutral)
    rows = []
    for direction in ("bullish", "bearish"):
        group = use[use["call"] == direction]
        if group.empty:
            continue
        returns = group[f"{_kind(neutral)}_coordinator"]
        rows.append({
            "call": direction, "calls": len(group), "effective": float(group["weight"].sum()),
            "hit_rate": _weighted_mean((returns > 0).astype(float), group["weight"]),
            "mean_return": _weighted_mean(returns, group["weight"]),
        })
    return pd.DataFrame(rows)


def by_conviction(frame: pd.DataFrame, *, neutral: bool) -> pd.DataFrame:
    """The coordinator's directional calls split by the conviction it stated: does a higher conviction do better?"""
    use = _usable(frame, neutral)
    use = use[(use["pos_coordinator"] != 0) & use["conviction"].notna()]
    rows = []
    for level, group in use.groupby("conviction"):
        returns = group[f"{_kind(neutral)}_coordinator"]
        rows.append({
            "conviction": int(level), "calls": len(group), "effective": float(group["weight"].sum()),
            "hit_rate": _weighted_mean((returns > 0).astype(float), group["weight"]),
            "mean_return": _weighted_mean(returns, group["weight"]),
        })
    return pd.DataFrame(rows, columns=["conviction", "calls", "effective", "hit_rate", "mean_return"])


def evaluate_pass_bar(frame: pd.DataFrame, bar: PassBar = PASS_BAR) -> dict:
    """The verdict at the 1-week horizon, market-neutral: "pass", "fail", or "not_yet" while the bar's sample is short.

    `frame` is the 1-week rows from `build_rows`.
    """
    use = _usable(frame, True) if not frame.empty else frame
    if use.empty:
        return {"verdict": "not_yet", "effective_weeks": 0.0, "non_falling_weeks": 0, "difference": None,
                "interval": None, "reasons": ["no scored calls yet"]}
    effective_weeks = float(use["weight"].sum()) / len(COMMODITIES)
    weekly = use.groupby("week")["ret"].mean()
    non_falling = int((weekly >= 0).sum())
    diff = use["neutral_coordinator"] - use["neutral_trend"]
    difference = _weighted_mean(diff, use["weight"])
    interval = bootstrap_interval(use, diff, confidence=bar.confidence)
    reasons = []
    if effective_weeks < bar.min_effective_weeks:
        reasons.append(f"{effective_weeks:.1f} of {bar.min_effective_weeks:g} effective weeks")
    if non_falling < bar.min_non_falling_weeks:
        reasons.append(f"{non_falling} of {bar.min_non_falling_weeks} weeks in which the basket did not fall")
    if reasons:
        verdict = "not_yet"
    else:
        verdict = "pass" if interval[0] > 0 else "fail"
        if verdict == "fail":
            reasons.append("the interval of the difference to the trend rule includes zero or below")
    return {"verdict": verdict, "effective_weeks": effective_weeks, "non_falling_weeks": non_falling,
            "difference": difference, "interval": interval, "reasons": reasons}


# --- challenger vs the official calls (ROADMAP.md Step 1) -------------------------------------------------------

ANALYSTS = ("technical", "news", "weather", "supply_demand")
COMPARED = ("coordinator", "official", "always_long", "always_short", "trend")


def _challenger_run(path: Path) -> dict | None:
    """What a challenger rerun's log says: its call, conviction, version and each analyst's bias."""
    events = [json.loads(line) for line in path.read_text().splitlines() if line]
    call = _logged_call(path)
    if call is None:
        return None
    outputs = {}
    for event in events:
        if event["event_type"] == "output" and event["agent"] in ANALYSTS:
            outputs[event["agent"]] = event["payload"]
    biases = {f"bias_{a}": outputs.get(a, {}).get("bias") for a in ANALYSTS}
    return {**call, **biases, "code_bias_technical": outputs.get("technical", {}).get("code_bias")}


def compare_challenger(frame: pd.DataFrame, challenger_logs: Path) -> tuple[pd.DataFrame, dict]:
    """The official rows that the challenger also re-made, with its calls beside the official ones.

    Everything else (entry, exit, baselines, basket) is the official row's, so the two are scored on the identical
    cycles and prices. In the returned frame `coordinator` is the challenger's call and `official` the logged one;
    each analyst's bias is in `bias_<analyst>` (and the technical analyst's mechanical one in `code_bias_technical`).
    Returns that frame and {"official": n, "matched": n, "missing": n}.
    """
    rows = []
    for _, row in frame.iterrows():
        path = Path(challenger_logs) / f"{row['run_id']}.jsonl"
        rerun = _challenger_run(path) if path.exists() else None
        if rerun is not None:
            rows.append({**row.to_dict(), "pos_official": row["pos_coordinator"], "call_official": row["call"],
                         "conviction_official": row["conviction"], "call": rerun["call"],
                         "conviction": rerun["conviction"], "version": rerun["version"],
                         "pos_coordinator": {"bullish": 1, "bearish": -1, "neutral": 0}[rerun["call"]],
                         **{k: v for k, v in rerun.items() if k.startswith(("bias_", "code_bias_"))}})
    info = {"official": len(frame), "matched": len(rows), "missing": len(frame) - len(rows)}
    if not rows:
        return pd.DataFrame(), info
    matched = pd.DataFrame(rows)
    in_week = matched.groupby(["commodity", "week"])["ret"].transform("size")
    matched["weight"] = 1 / in_week.clip(lower=EXPECTED_CALLS_PER_WEEK)
    for strategy in ("coordinator", "official"):
        matched[f"raw_{strategy}"] = matched[f"pos_{strategy}"] * matched["ret"]
        matched[f"neutral_{strategy}"] = matched[f"pos_{strategy}"] * (matched["ret"] - matched["basket"])
    return matched, info


def agreement(matched: pd.DataFrame) -> dict:
    """How often the challenger made the same call as the official run, and how it differed when it did not."""
    if matched.empty:
        return {}
    same = matched["pos_coordinator"] == matched["pos_official"]
    flip = matched["pos_coordinator"] * matched["pos_official"] == -1
    return {
        "same_call": float(same.mean()), "opposite_call": float(flip.mean()),
        "neutral_in_one": float((~same & ~flip).mean()),
        "to_neutral": float(((matched["pos_coordinator"] == 0) & (matched["pos_official"] != 0)).mean()),
        "from_neutral": float(((matched["pos_coordinator"] != 0) & (matched["pos_official"] == 0)).mean()),
    }


def challenger_difference(matched: pd.DataFrame, *, neutral: bool) -> dict:
    """Challenger minus official, weighted mean return per call, with its bootstrap interval."""
    use = _usable(matched, neutral)
    if use.empty:
        return {"difference": None, "interval": None}
    diff = use[f"{_kind(neutral)}_coordinator"] - use[f"{_kind(neutral)}_official"]
    return {"difference": _weighted_mean(diff, use["weight"]), "interval": bootstrap_interval(use, diff)}


def by_analyst(matched: pd.DataFrame, *, neutral: bool) -> pd.DataFrame:
    """Each analyst's bias scored as a position (the sign of its bias) against the same returns.

    The technical analyst appears twice: its final bias, and the mechanical `code_bias` it started from, so the
    effect of the model's one-step adjustments is visible. A bias of 0 is no view: it counts as a call, earns 0, and
    is left out of the hit rate.
    """
    use = _usable(matched, neutral)
    columns = ["analyst", "calls", "effective", "directional", "hit_rate", "mean_return"]
    series = {**{a: f"bias_{a}" for a in ANALYSTS}, "technical (code only)": "code_bias_technical"}
    out = []
    for name, column in series.items():
        if column not in use:
            continue
        group = use[use[column].notna()]
        if group.empty:
            continue
        position = group[column].apply(lambda b: (b > 0) - (b < 0))
        returns = position * (group["ret"] - group["basket"] if neutral else group["ret"])
        directional = position != 0
        out.append({
            "analyst": name, "calls": len(group), "effective": float(group["weight"].sum()),
            "directional": int(directional.sum()),
            "hit_rate": _weighted_mean((returns[directional] > 0).astype(float), group.loc[directional, "weight"])
            if directional.any() else float("nan"),
            "mean_return": _weighted_mean(returns, group["weight"]),
        })
    return pd.DataFrame(out, columns=columns)


def official_view(matched: pd.DataFrame) -> pd.DataFrame:
    """The matched rows with the official calls in the `coordinator` columns, to score them with the same code."""
    view = matched.copy()
    for column in ("pos", "raw", "neutral"):
        view[f"{column}_coordinator"] = matched[f"{column}_official"]
    view["call"], view["conviction"] = matched["call_official"], matched["conviction_official"]
    return view

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from ags.agents.output import of, parse_reply
from ags.llm.loop import ChatClient, ToolSpec, run_json_loop
from ags.tools.chart import get_chart_snapshot as compute_chart_snapshot
from ags.tools.pit_store import ET
from ags.tools.seasonality import get_seasonality as compute_seasonality
from ags.tools.sources.prices import (
    INTRADAY_MAX_DAYS,
    INTRADAY_TIMEFRAMES,
    LIVE_CYCLE_MAX_AGE,
    PricesClient,
    get_intraday_bars,
)
from ags.tools.sources.prices import get_prices as fetch_prices
from ags.tools.trend import get_trend_state as compute_trend_state
from ags.tools.volatility import get_volatility as compute_volatility

_REPLY_FIELDS = {"seasonality_alignment": of(str), "key_levels": of(dict), "chart_description": of(str)}
_DEFAULT_HISTORY_DAYS = 365 * 3
_DEFAULT_CHART_LOOKBACK_CANDLES = 30
_MAX_INTRADAY_BARS = 200

SYSTEM_PROMPT_TEMPLATE = """You are the Technical analyst for {commodity} futures.

Trend and volatility below are computed deterministically by code — treat
them as known facts, not something to recompute. Your job is judgment:
assess whether the current move is typical or atypical for this point in
the calendar (use the get_seasonality tool), identify key price levels
(use the get_prices tool to inspect recent OHLC; its "tf" argument
selects the timeframe: "D1" daily bars (the default) or the lower timeframes
"H1", "M30" and "M15" for how the days and the last sessions were built.
A lower-timeframe request returns only completed candles, newest {max_bars}
at most, with UTC open times), and describe what the
chart looks like from the raw candles and EMAs below.

Known facts as of {as_of}:
  trend: {trend}
  volatility: {volatility}
  latest {chart_lookback_candles} candles (OHLCV + EMAs): {chart_snapshot}

{intraday}

Respond with a JSON object containing exactly these fields:
  "seasonality_alignment": "typical" | "atypical" (with implicit reasoning)
  "key_levels": {{"recent_range": [low, high], "breakout_points": [...]}}
  "chart_description": a short narrative of the recent candle/EMA action
No other text — JSON only."""


def describe_intraday(reference_quote: dict | None, last_bar: dict | None) -> str:
    """The prompt line for the live quote. The model must not mistake it for a closed bar."""
    if not reference_quote:
        return "Intraday price: unavailable this cycle. Everything above is from closed daily sessions."
    text = (
        f"INTRADAY price (a live quote captured {reference_quote['captured_at']}, not a closed daily bar): "
        f"bid {reference_quote['bid']}, ask {reference_quote['ask']}, mid {reference_quote['price']:.5g}. "
        "The trend, volatility and candles above use closed sessions only and do not include this move."
    )
    if last_bar:
        move = reference_quote["price"] / last_bar["close"] - 1
        text += f" Versus the last closed session ({last_bar['date']}, close {last_bar['close']}): {move:+.2%}."
    return text


def _bound(value: str, is_end: bool) -> datetime:
    """An ISO date or datetime as UTC. A bare end date includes that whole day."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if is_end and len(value) <= 10:
        parsed = datetime.combine(parsed.date(), time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return parsed


def _closes_series(bars: list[dict]) -> pd.Series:
    # pd.Timestamp keys (not datetime.date) are required so the resulting
    # index is a real DatetimeIndex — get_seasonality calls .isocalendar()
    # on it, which a plain object Index doesn't support.
    trading_bars = [bar for bar in bars if not bar.get("no_trading")]
    return pd.Series(
        {pd.Timestamp(bar["date"]): bar["close"] for bar in trading_bars}
    ).sort_index()


def run_technical_analyst(
    data_dir: Path,
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: datetime,
    model: str,
    prices_client: PricesClient | None = None,
    reference_quote: dict | None = None,
    now: datetime | None = None,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    chart_lookback_candles: int = _DEFAULT_CHART_LOOKBACK_CANDLES,
) -> dict:
    degraded = False
    trend = None
    volatility = None
    series = None
    chart_snapshot = None
    last_bar = None

    as_of_day = as_of.astimezone(ET).date()
    history_start = as_of_day - timedelta(days=history_days)
    try:
        history_bars = fetch_prices(
            data_dir, commodity=commodity, start=history_start, end=as_of_day, as_of=as_of, client=prices_client
        )
        series = _closes_series(history_bars)
        trading = [b for b in history_bars if not b.get("no_trading")]
        last_bar = trading[-1] if trading else None
        trend = compute_trend_state(series)
        volatility = compute_volatility(series)
        chart_snapshot = compute_chart_snapshot(history_bars, chart_lookback_candles)
    except Exception:
        degraded = True

    # Raises on a bad date, an unsupported timeframe or a source failure: the loop reports it and asks the model to retry.
    def _get_prices_tool(start: str, end: str, tf: str = "D1") -> dict:
        tf = (tf or "D1").upper()
        if tf != "D1":
            return _intraday_tool(start, end, tf)
        bars = fetch_prices(
            data_dir,
            commodity=commodity,
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            as_of=as_of,
            client=prices_client,
        )
        return {"bars": bars}

    clock = now or datetime.now(timezone.utc)
    # A live cycle reads fresh completed bars; a replayed one only what was captured by its own as_of.
    live = clock - as_of <= LIVE_CYCLE_MAX_AGE

    def _intraday_tool(start: str, end: str, tf: str) -> dict:
        if tf not in INTRADAY_TIMEFRAMES:
            raise ValueError(f"unsupported tf {tf!r}; use D1, {', '.join(INTRADAY_TIMEFRAMES)}")
        at = clock if live else as_of
        lo, hi = _bound(start, False), _bound(end, True)
        earliest = at - timedelta(days=INTRADAY_MAX_DAYS[tf])
        bars = get_intraday_bars(
            data_dir, commodity=commodity, tf=tf, start=max(lo, earliest), end=hi, as_of=at,
            client=prices_client, now=clock if live else None,
        )
        result = {"tf": tf, "bars": bars[-_MAX_INTRADAY_BARS:]}
        if lo < earliest:
            result["note"] = f"start moved to {earliest.date()}: {tf} history is limited to {INTRADAY_MAX_DAYS[tf]} days"
        if len(bars) > _MAX_INTRADAY_BARS:
            result["note"] = (result.get("note", "") + f" Showing the newest {_MAX_INTRADAY_BARS} of {len(bars)} bars.").strip()
        return result

    def _get_seasonality_tool() -> dict:
        nonlocal degraded
        if series is None:
            degraded = True
            return {"error": "price history unavailable"}
        return compute_seasonality(series, as_of)

    tools = [
        ToolSpec(
            name="get_prices",
            description=(
                "OHLC candles for this commodity over a range, completed candles only. tf=D1 (default) gives "
                "roll-adjusted daily bars; tf=H1, M30 or M15 give lower-timeframe bars (UTC open times, newest "
                f"{_MAX_INTRADAY_BARS} at most, look-back limited per timeframe)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "ISO date or datetime (UTC if no offset), e.g. 2026-01-05"},
                    "end": {"type": "string", "description": "ISO date or datetime, e.g. 2026-01-10 (a date includes that day)"},
                    "tf": {
                        "type": "string", "enum": ["D1", *INTRADAY_TIMEFRAMES],
                        "description": "timeframe, default D1",
                    },
                },
                "required": ["start", "end"],
            },
            function=_get_prices_tool,
        ),
        ToolSpec(
            name="get_seasonality",
            description="Historical mean/median return for the current ISO calendar week across prior years.",
            parameters={"type": "object", "properties": {}},
            function=_get_seasonality_tool,
        ),
    ]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        commodity=commodity,
        as_of=as_of.isoformat(),
        trend=trend,
        volatility=volatility,
        chart_lookback_candles=chart_lookback_candles,
        max_bars=_MAX_INTRADAY_BARS,
        intraday=describe_intraday(reference_quote, last_bar),
        chart_snapshot=json.dumps(chart_snapshot, default=str)
        if chart_snapshot is not None
        else "unavailable (price history could not be fetched)",
    )
    user_prompt = f"Assess the technical picture for {commodity} as of {as_of.isoformat()}."

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    # A malformed reply only loses the model's fields: trend and volatility were computed by code.
    parsed, complete, unparsed = parse_reply(loop_result.content, _REPLY_FIELDS)
    degraded = degraded or not complete or loop_result.tool_errors > 0

    return {
        "trend": trend,
        "volatility": volatility,
        "seasonality_alignment": parsed.get("seasonality_alignment"),
        "key_levels": parsed.get("key_levels"),
        "chart_description": parsed.get("chart_description"),
        **unparsed,
        "degraded": degraded,
    }

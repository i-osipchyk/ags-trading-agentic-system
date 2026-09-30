import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ags.llm.loop import ChatClient, ToolSpec, run_loop
from ags.tools.chart import get_chart_snapshot as compute_chart_snapshot
from ags.tools.seasonality import get_seasonality as compute_seasonality
from ags.tools.sources.prices import PricesClient
from ags.tools.sources.prices import get_prices as fetch_prices
from ags.tools.trend import get_trend_state as compute_trend_state
from ags.tools.volatility import get_volatility as compute_volatility

_DEFAULT_HISTORY_DAYS = 365 * 3
_DEFAULT_CHART_LOOKBACK_CANDLES = 30

SYSTEM_PROMPT_TEMPLATE = """You are the Technical analyst for {commodity} futures.

Trend and volatility below are computed deterministically by code — treat
them as known facts, not something to recompute. Your job is judgment:
assess whether the current move is typical or atypical for this point in
the calendar (use the get_seasonality tool), identify key price levels
(use the get_prices tool to inspect recent OHLC), and describe what the
chart looks like from the raw candles and EMAs below.

Known facts as of {as_of}:
  trend: {trend}
  volatility: {volatility}
  latest {chart_lookback_candles} candles (OHLCV + EMAs): {chart_snapshot}

Respond with a JSON object containing exactly these fields:
  "seasonality_alignment": "typical" | "atypical" (with implicit reasoning)
  "key_levels": {{"recent_range": [low, high], "breakout_points": [...]}}
  "chart_description": a short narrative of the recent candle/EMA action
No other text — JSON only."""


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
    as_of: date,
    model: str,
    prices_client: PricesClient | None = None,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    chart_lookback_candles: int = _DEFAULT_CHART_LOOKBACK_CANDLES,
) -> dict:
    degraded = False
    trend = None
    volatility = None
    series = None
    chart_snapshot = None

    history_start = as_of - timedelta(days=history_days)
    try:
        history_bars = fetch_prices(
            data_dir, commodity=commodity, start=history_start, end=as_of, as_of=as_of, client=prices_client
        )
        series = _closes_series(history_bars)
        trend = compute_trend_state(series)
        volatility = compute_volatility(series)
        chart_snapshot = compute_chart_snapshot(history_bars, chart_lookback_candles)
    except Exception:
        degraded = True

    def _get_prices_tool(start: str, end: str) -> dict:
        nonlocal degraded
        try:
            bars = fetch_prices(
                data_dir,
                commodity=commodity,
                start=date.fromisoformat(start),
                end=date.fromisoformat(end),
                as_of=as_of,
                client=prices_client,
            )
            return {"bars": bars}
        except Exception as exc:
            degraded = True
            return {"error": str(exc)}

    def _get_seasonality_tool() -> dict:
        nonlocal degraded
        if series is None:
            degraded = True
            return {"error": "price history unavailable"}
        return compute_seasonality(series, as_of)

    tools = [
        ToolSpec(
            name="get_prices",
            description="Roll-adjusted daily OHLC bars for this commodity over a date range.",
            parameters={
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "ISO date, e.g. 2026-01-05"},
                    "end": {"type": "string", "description": "ISO date, e.g. 2026-01-10"},
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
        chart_snapshot=json.dumps(chart_snapshot, default=str)
        if chart_snapshot is not None
        else "unavailable (price history could not be fetched)",
    )
    user_prompt = f"Assess the technical picture for {commodity} as of {as_of.isoformat()}."

    loop_result = run_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )

    parsed = json.loads(loop_result.content)

    return {
        "trend": trend,
        "volatility": volatility,
        "seasonality_alignment": parsed.get("seasonality_alignment"),
        "key_levels": parsed.get("key_levels"),
        "chart_description": parsed.get("chart_description"),
        "degraded": degraded,
    }

import json
from datetime import date, timedelta

from ags.agents.technical import run_technical_analyst


class FakeChatClient:
    """Stub model client: returns scripted messages in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, *, model):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return self._responses.pop(0)


class FakePricesClient:
    def __init__(self, bars_by_symbol):
        self._bars_by_symbol = bars_by_symbol
        self.calls = []

    def get_daily_bars(self, symbol_name, start, end):
        self.calls.append((symbol_name, start, end))
        return [bar for bar in self._bars_by_symbol[symbol_name] if start <= date.fromisoformat(bar["date"]) <= end]


class FailingPricesClient:
    def get_daily_bars(self, symbol_name, start, end):
        raise ConnectionError("cTrader connection refused")


def _rising_corn_bars(as_of: date, flat_days: int = 400, rise_days: int = 150) -> list[dict]:
    # Exact same values as tests/tools/test_trend.py's sustained-rally fixture
    # (relative slope matters for the EWMA band crossing, not just shape), so
    # its already-verified {state, age} output transfers here unchanged.
    values = [100.0] * flat_days + [100.0 + i for i in range(1, rise_days + 1)]
    total_days = flat_days + rise_days
    start = as_of - timedelta(days=total_days - 1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "open": v,
            "high": v,
            "low": v,
            "close": v,
            "volume": 100,
        }
        for i, v in enumerate(values)
    ]


def test_run_technical_analyst_returns_schema_conformant_output_for_one_symbol(tmp_path):
    as_of = date(2026, 6, 5)
    bars = _rising_corn_bars(as_of)
    prices_client = FakePricesClient({"Corn": bars})

    chat_client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "seasonality_alignment": "typical",
                        "key_levels": {"recent_range": [4.40, 4.65], "breakout_points": [4.70]},
                        "chart_description": "Price has grinded steadily higher over the last 30 sessions, "
                        "holding above all EMAs with no material pullback.",
                    }
                ),
                "tool_calls": None,
            }
        ]
    )

    result = run_technical_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        prices_client=prices_client,
        history_days=550,
        chart_lookback_candles=30,
    )

    assert set(result.keys()) == {
        "trend",
        "volatility",
        "seasonality_alignment",
        "key_levels",
        "chart_description",
        "degraded",
    }

    # Trend/volatility are code-computed facts, not derived by the LLM.
    assert result["trend"] == {
        "short": {"state": "up", "age": 148},
        "medium": {"state": "up", "age": 147},
        "long": {"state": "up", "age": 146},
    }
    assert isinstance(result["volatility"]["current"], float)
    assert isinstance(result["volatility"]["trailing_average"], float)
    assert result["volatility"]["current"] >= 0
    assert result["volatility"]["trailing_average"] >= 0

    # Judgment-call fields come from the LLM's structured output.
    assert result["seasonality_alignment"] == "typical"
    assert result["key_levels"] == {"recent_range": [4.40, 4.65], "breakout_points": [4.70]}
    assert result["chart_description"] == (
        "Price has grinded steadily higher over the last 30 sessions, "
        "holding above all EMAs with no material pullback."
    )

    assert result["degraded"] is False

    # The raw candle+EMA data (the chart "fact") must reach the prompt so
    # the LLM has something to describe — not just be computed and dropped.
    system_message = chat_client.calls[0]["messages"][0]
    assert system_message["role"] == "system"
    assert '"ema_8"' in system_message["content"]
    assert bars[-1]["date"] in system_message["content"]


def test_run_technical_analyst_surfaces_degraded_true_on_a_source_failure_instead_of_aborting(tmp_path):
    as_of = date(2026, 6, 5)

    chat_client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": json.dumps({"seasonality_alignment": "unknown", "key_levels": {}, "chart_description": None}),
                "tool_calls": None,
            }
        ]
    )

    result = run_technical_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        prices_client=FailingPricesClient(),
        history_days=550,
    )

    assert result["degraded"] is True
    assert result["trend"] is None
    assert result["volatility"] is None

    # No price history means no chart data to hand the LLM either — the
    # system prompt should say so rather than silently omit it.
    system_message = chat_client.calls[0]["messages"][0]
    assert "unavailable" in system_message["content"]

import json
from datetime import date, timedelta, datetime

from ags.agents.technical import run_technical_analyst
from ags.tools.pit_store import ET


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
    # cTrader stamps a bar with the evening before its session, and only sessions
    # before as_of are public — so the newest usable stamp is as_of - 2.
    start = as_of - timedelta(days=total_days + 1)
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
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    bars = _rising_corn_bars(as_of.date())
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
        "positioning",
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
    assert (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat() in system_message["content"]


def test_run_technical_analyst_surfaces_degraded_true_on_a_source_failure_instead_of_aborting(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)

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


def test_run_technical_analyst_degrades_instead_of_raising_when_the_model_never_returns_json(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    chat_client = FakeChatClient(
        responses=[{"role": "assistant", "content": "I could not produce JSON.", "tool_calls": None}] * 2
    )

    result = run_technical_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}),
        history_days=550,
    )

    assert result["degraded"] is True
    assert result["seasonality_alignment"] is None
    assert result["key_levels"] is None
    assert result["chart_description"] is None
    # Trend and volatility are computed by code, so they survive the model failure.
    assert result["trend"] is not None
    assert result["volatility"] is not None


def test_run_technical_analyst_retries_once_when_the_first_reply_is_not_json(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    good = {"seasonality_alignment": "typical", "key_levels": {}, "chart_description": "flat"}
    chat_client = FakeChatClient(
        responses=[
            {"role": "assistant", "content": "", "tool_calls": None},
            {"role": "assistant", "content": json.dumps(good), "tool_calls": None},
        ]
    )

    result = run_technical_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model="deepseek-chat",
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}),
        history_days=550,
    )

    assert result["degraded"] is False
    assert result["seasonality_alignment"] == "typical"


def test_run_technical_analyst_keeps_code_facts_when_the_reply_is_json_but_not_an_object(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    chat_client = FakeChatClient(responses=[{"role": "assistant", "content": '["typical"]', "tool_calls": None}])

    result = run_technical_analyst(
        tmp_path, chat_client, commodity="corn", as_of=as_of, model="deepseek-chat",
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}), history_days=550,
    )

    assert result["degraded"] is True
    assert result["trend"] is not None and result["volatility"] is not None


def test_run_technical_analyst_degrades_a_reply_with_a_mistyped_field_but_keeps_the_good_ones(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    reply = {"seasonality_alignment": "typical", "key_levels": "around 450", "chart_description": "steady"}
    chat_client = FakeChatClient(responses=[{"role": "assistant", "content": json.dumps(reply), "tool_calls": None}])

    result = run_technical_analyst(
        tmp_path, chat_client, commodity="corn", as_of=as_of, model="deepseek-chat",
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}), history_days=550,
    )

    assert result["degraded"] is True
    assert result["key_levels"] is None
    assert result["seasonality_alignment"] == "typical"

# --- get_prices with a lower timeframe ---

FINAL = {"role": "assistant", "tool_calls": None, "content": json.dumps({
    "seasonality_alignment": "typical", "key_levels": {"recent_range": [1, 2], "breakout_points": []},
    "chart_description": "x",
})}


class IntradayPricesClient(FakePricesClient):
    """Daily bars plus a 15-minute bar for every quarter hour in the requested range."""

    def get_bars(self, symbol_name, tf, start, end):
        self.calls.append(("bars", tf, start, end))
        out, t = [], start
        while t < end:
            out.append({"timestamp": t.isoformat(), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1})
            t += timedelta(minutes=15)
        return out


def _ask_for(tmp_path, arguments, *, now, as_of=datetime(2026, 6, 5, 6, 0, tzinfo=ET)):
    client = IntradayPricesClient({"Corn": _rising_corn_bars(as_of.date())})
    call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "get_prices", "arguments": json.dumps(arguments)}}
    ]}
    chat = FakeChatClient([call, FINAL])
    run_technical_analyst(
        tmp_path, chat, commodity="corn", as_of=as_of, model="m", prices_client=client, now=now, history_days=550
    )
    tool_message = next(m for m in chat.calls[1]["messages"] if m["role"] == "tool")
    return json.loads(tool_message["content"]), chat, client


LIVE_NOW = datetime(2026, 6, 5, 6, 0, 20, tzinfo=ET)


def test_the_tool_advertises_the_timeframes_and_the_prompt_explains_them(tmp_path):
    _, chat, _ = _ask_for(tmp_path, {"start": "2026-06-05", "end": "2026-06-05", "tf": "H1"}, now=LIVE_NOW)

    system = chat.calls[0]["messages"][0]["content"]
    assert '"tf"' in system and "H1" in system and "completed" in system
    tool = next(t for t in chat.calls[0]["tools"] if t["function"]["name"] == "get_prices")
    assert tool["function"]["parameters"]["properties"]["tf"]["enum"] == ["D1", "M15", "M30", "H1"]


def test_an_hourly_request_returns_completed_utc_bars_and_includes_the_end_day(tmp_path):
    result, _, client = _ask_for(tmp_path, {"start": "2026-06-05", "end": "2026-06-05", "tf": "m15"}, now=LIVE_NOW)

    assert result["tf"] == "M15"
    stamps = [b["timestamp"] for b in result["bars"]]
    assert stamps[0].startswith("2026-06-05T00:00") and stamps[-1].startswith("2026-06-05T09:45")  # 06:00 EDT = 10:00 UTC
    assert any(c[0] == "bars" and c[1] == "M15" for c in client.calls)


def test_a_long_intraday_request_is_capped_and_says_so(tmp_path):
    result, _, _ = _ask_for(tmp_path, {"start": "2026-06-01", "end": "2026-06-05", "tf": "M15"}, now=LIVE_NOW)

    assert len(result["bars"]) == 200 and "newest 200" in result["note"]


def test_an_unknown_timeframe_is_an_error_the_model_can_read(tmp_path):
    result, _, client = _ask_for(tmp_path, {"start": "2026-06-05", "end": "2026-06-05", "tf": "M1"}, now=LIVE_NOW)

    assert "unsupported tf" in result["error"] and not any(c[0] == "bars" for c in client.calls)


def test_a_replayed_run_never_fetches_lower_timeframe_bars_live(tmp_path):
    long_after = datetime(2026, 6, 9, tzinfo=ET)
    result, _, client = _ask_for(tmp_path, {"start": "2026-06-05", "end": "2026-06-05", "tf": "H1"}, now=long_after)

    assert result["bars"] == [] and not any(c[0] == "bars" for c in client.calls)


class FakeCotClient:
    def __init__(self, rows):
        self._rows = rows

    def get_reports(self, symbol, *, start, end):
        return list(self._rows)


def _cot_row(report_date: str, long: int, short: int) -> dict:
    return {"report_date": report_date, "open_interest": 1000, "managed_money_long": long, "managed_money_short": short}


def test_run_technical_analyst_adds_the_latest_released_positioning_as_a_computed_fact(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)  # Friday: the Tue 2026-06-02 report is out at 15:30 ET
    # Managed-money net of 10%, 30% and (latest) 20% of open interest: 2 of the 3 weeks are at or below 20%.
    cot_client = FakeCotClient(
        [_cot_row("2026-05-19", 110, 10), _cot_row("2026-05-26", 330, 30), _cot_row("2026-06-02", 300, 100)]
    )
    chat_client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": json.dumps({"seasonality_alignment": "typical", "key_levels": {}, "chart_description": "flat"}),
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
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}),
        history_days=550,
        cot_client=cot_client,
    )

    expected = {
        "report_date": "2026-06-02",
        "released": "2026-06-05",
        "managed_money_net": 200,
        "open_interest": 1000,
        "net_pct_oi": 20.0,
        "percentile": 66.67,
        "weeks": 3,
    }
    assert result["positioning"] == expected
    assert json.dumps(expected) in chat_client.calls[0]["messages"][0]["content"]


class FailingCotClient:
    def get_reports(self, symbol, *, start, end):
        raise ConnectionError("publicreporting.cftc.gov unreachable")


def test_run_technical_analyst_degrades_but_keeps_everything_else_when_positioning_is_unavailable(tmp_path):
    as_of = datetime(2026, 6, 5, 16, 0, tzinfo=ET)
    chat_client = FakeChatClient(
        responses=[
            {
                "role": "assistant",
                "content": json.dumps({"seasonality_alignment": "typical", "key_levels": {}, "chart_description": "flat"}),
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
        prices_client=FakePricesClient({"Corn": _rising_corn_bars(as_of.date())}),
        history_days=550,
        cot_client=FailingCotClient(),
    )

    assert result["positioning"] is None
    assert result["degraded"] is True
    assert result["trend"]["short"]["state"] == "up"  # the price-derived facts are untouched
    assert "positioning" in chat_client.calls[0]["messages"][0]["content"]

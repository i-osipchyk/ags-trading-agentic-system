from datetime import date, datetime, time, timedelta

import pytest

from ags.tools.pit_store import ET
from ags.agents.technical import run_technical_analyst
from ags.config import Config
from ags.llm.client import LLMClient


def _last_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.mark.integration
def test_run_technical_analyst_standalone_run_for_corn_against_real_sources(tmp_path):
    config = Config.from_env()
    chat_client = LLMClient().bind("technical", test="integration")
    as_of = datetime.combine(_last_weekday(date.today() - timedelta(days=1)), time(16, 0), tzinfo=ET)

    result = run_technical_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model=config.deepseek_model,
        chart_lookback_candles=config.chart_lookback_candles,
    )

    assert set(result.keys()) == {
        "trend",
        "volatility",
        "seasonality_alignment",
        "key_levels",
        "chart_description",
        "degraded",
    }
    assert result["degraded"] is False
    assert set(result["trend"].keys()) == {"short", "medium", "long"}
    for timeframe in result["trend"].values():
        assert timeframe["state"] in {"up", "down", "sideways"}
    assert result["volatility"]["current"] >= 0
    assert result["volatility"]["trailing_average"] >= 0
    assert result["seasonality_alignment"]
    assert isinstance(result["key_levels"], dict)
    assert result["chart_description"]

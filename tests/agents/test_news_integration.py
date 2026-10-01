from datetime import date, timedelta

import pytest

from ags.agents.news import run_news_analyst
from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient


def _last_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.mark.integration
def test_run_news_analyst_standalone_run_for_corn_against_real_sources(tmp_path):
    config = Config.from_env()
    chat_client = DeepSeekChatClient(config)
    as_of = _last_weekday(date.today() - timedelta(days=1))

    result = run_news_analyst(
        tmp_path,
        chat_client,
        commodity="corn",
        as_of=as_of,
        model=config.deepseek_model,
    )

    assert set(result.keys()) == {"headlines", "degraded"}
    assert isinstance(result["headlines"], list)
    for item in result["headlines"]:
        assert set(item.keys()) == {"headline", "source", "date", "direction", "reason"}
        assert item["direction"] in {"bullish", "bearish", "neutral"}

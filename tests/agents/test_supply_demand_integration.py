from datetime import date, timedelta

import pytest

from ags.agents.supply_demand import run_supply_demand_analyst
from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient


def _last_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.mark.integration
def test_run_supply_demand_analyst_standalone_run_for_corn_against_real_sources(tmp_path):
    config = Config.from_env()
    chat_client = DeepSeekChatClient(config)
    as_of = _last_weekday(date.today() - timedelta(days=1))

    result = run_supply_demand_analyst(
        tmp_path, chat_client, commodity="corn", as_of=as_of, model=config.deepseek_model
    )

    assert set(result.keys()) == {"latest_report", "key_figures", "stocks_to_use", "degraded"}
    assert isinstance(result["key_figures"], list)

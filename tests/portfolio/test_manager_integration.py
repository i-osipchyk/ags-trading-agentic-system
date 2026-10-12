from datetime import datetime

import pytest

from ags.config import Config
from ags.llm.client import LLMClient
from ags.portfolio.guardrails import GuardrailConfig, apply_guardrails
from ags.portfolio.manager import run_portfolio_manager
from ags.tools.pit_store import ET
from tests.portfolio.test_manager import AS_OF, CALLS, LOTS, RISK, SYMBOLS


@pytest.mark.integration
def test_live_manager_answers_canned_inputs_with_a_schema_valid_proposal():
    config = Config.from_env()

    result = run_portfolio_manager(
        LLMClient().bind("manager", test="integration"), as_of=AS_OF, model=config.deepseek_model,
        calls=CALLS, lots=LOTS, risk=RISK, equity=10_000.0, free_margin=8_500.0,
    )

    specs = {s: {"lot_size": 100_000, "min_volume": 10_000, "step_volume": 10_000, "margin_rate": 0.1} for s in SYMBOLS}
    _, violations = apply_guardrails(
        result["proposal"], {"equity": 10_000.0, "lots": LOTS}, CALLS, specs, RISK,
        GuardrailConfig(1.0, 1.5, 3.0, 0.4, 0.2), False,
    )
    assert result["proposal"] is not None
    assert "schema" not in [v["rule"] for v in violations]

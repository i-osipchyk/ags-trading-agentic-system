import json
from datetime import datetime

from ags.llm.loop import ChatClient, run_json_loop

SYSTEM_PROMPT_TEMPLATE = """You are the Portfolio Manager for a paper book of seven agricultural futures CFDs, as of {as_of}.

You decide how much to hold and where, given the seven coordinator calls below. You do not
re-research or second-guess the calls' direction: you size. Code handles unit conversion, caps,
fills and costs after you. Corn, soybeans and wheat move together; do not treat them as three
independent bets. A neutral call freezes its leg: whatever you return for it is ignored.

Risk is comparable across symbols through "atr_usd_per_lot", the dollar value of one ATR move per lot.
A symbol whose risk facts are null couldn't be measured this cycle: its leg is held too.

Respond with a JSON object containing exactly these fields:
  "legs": one entry per symbol, all seven present, each {{"symbol": ..., "delta_lots": signed number added to the current position, "rationale": short text}}
  "portfolio_note": short cross-commodity reasoning
No other text — JSON only."""

_CALL_FIELDS = ("call", "conviction", "thesis", "key_drivers")
_RISK_FIELDS = ("atr", "atr_usd_per_lot")


def _manager_inputs(calls, lots, risk, equity, free_margin) -> dict:
    return {
        "calls": {s: None if c is None else {f: c[f] for f in _CALL_FIELDS} for s, c in calls.items()},
        "current_lots": lots,
        "risk": {s: {f: r[f] for f in _RISK_FIELDS} for s, r in risk.items()},
        "equity": equity,
        "free_margin": free_margin,
    }


def run_portfolio_manager(
    chat_client: ChatClient,
    *,
    as_of: datetime,
    model: str,
    calls: dict,
    lots: dict,
    risk: dict,
    equity: float,
    free_margin: float,
) -> dict:
    """One LLM call, no tools. Returns the parsed proposal untouched: validating it is guardrail 1's job.

    `proposal` is None when the reply isn't a JSON object, which guardrail 1 treats as hold-all.
    """
    result = run_json_loop(
        chat_client,
        model=model,
        system_prompt=SYSTEM_PROMPT_TEMPLATE.format(as_of=as_of.isoformat()),
        user_prompt=json.dumps(_manager_inputs(calls, lots, risk, equity, free_margin), indent=2, default=str),
        tools=[],
    )
    try:
        proposal = json.loads(result.content)
    except json.JSONDecodeError:
        proposal = None
    if not isinstance(proposal, dict):
        proposal = None
    return {"proposal": proposal, "raw": result.content, "degraded": proposal is None}

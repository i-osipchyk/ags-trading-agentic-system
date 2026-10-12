_FULL_CONVICTION = 5


def shadow_proposal(calls: dict, book_state: dict, risk: dict, risk_budget: float) -> dict:
    """Mechanical sizer: the baseline the manager is scored against. No LLM, no rounding.

    target_lots = sign(call) * conviction/5 * risk_budget * equity / atr_usd_per_lot.
    Neutral and failed (None) commodities are held. The result has the
    manager's proposal shape so it goes through `apply_guardrails` unchanged.
    """
    legs = []
    for symbol, held in book_state["lots"].items():
        call = calls.get(symbol)
        if call is None or call["call"] == "neutral":
            delta, rationale = 0.0, "held: neutral or missing call"
        else:
            sign = 1 if call["call"] == "bullish" else -1
            budget = risk_budget * book_state["equity"] / risk[symbol]["atr_usd_per_lot"]
            delta = sign * call["conviction"] / _FULL_CONVICTION * budget - held
            rationale = "mechanical size"
        legs.append({"symbol": symbol, "delta_lots": delta, "rationale": rationale})
    return {"legs": legs, "portfolio_note": "shadow sizer"}

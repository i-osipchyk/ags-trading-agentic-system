from dataclasses import dataclass


_EPS = 1e-9
_GRAINS = ('corn', 'soybeans', 'wheat')


@dataclass(frozen=True)
class GuardrailConfig:
    per_commodity_cap: float
    grain_bucket_cap: float
    gross_leverage_cap: float
    margin_buffer: float
    min_resize_fraction: float


def _valid_deltas(proposal, symbols) -> dict | None:
    """Per-symbol deltas if the proposal is well formed (every symbol exactly once), else None."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("legs"), list):
        return None
    deltas = {}
    for leg in proposal["legs"]:
        symbol, delta = leg.get("symbol"), leg.get("delta_lots")
        if symbol in deltas or isinstance(delta, bool) or not isinstance(delta, (int, float)):
            return None
        deltas[symbol] = float(delta)
    return deltas if set(deltas) == set(symbols) else None


def apply_guardrails(
    proposal: dict | None,
    book_state: dict,
    calls: dict,
    specs: dict,
    risk: dict,
    config: GuardrailConfig,
    breaker_active: bool,
) -> tuple[dict[str, float], list[dict]]:
    """Clamp a manager proposal to the rules in DESIGN.md. Pure: no I/O.

    `book_state` is `{"equity": float, "lots": {symbol: signed lots}}`. Returns
    the delta (in lots) to apply per symbol, and the violations found.
    """
    current = dict(book_state["lots"])
    symbols = list(current)
    violations: list[dict] = []

    raw = _valid_deltas(proposal, symbols)
    if raw is None:
        violations.append({"rule": "schema", "symbol": None, "proposed": None, "applied": 0.0,
                           "detail": "proposal missing, malformed, or not exactly one leg per symbol"})
        return {s: 0.0 for s in symbols}, violations

    target = {s: current[s] + raw[s] for s in symbols}

    forced: set[str] = set()  # legs moved by a safety clamp; the no-churn rule must not undo those

    def clamp(rule: str, symbol: str, new_target: float, detail: str) -> None:
        if new_target != target[symbol]:
            if rule in ("missing_input", "direction", "per_commodity_cap"):
                forced.add(symbol)
            violations.append({"rule": rule, "symbol": symbol, "proposed": target[symbol] - current[symbol],
                               "applied": new_target - current[symbol], "detail": detail})
            target[symbol] = new_target

    # 9. missing input, then 2. direction (neutral frozen, wrong side closed)
    for s in symbols:
        call = calls.get(s)
        if call is None:
            clamp("missing_input", s, current[s], "pipeline failed; leg held as-is")
        elif call["call"] == "neutral":
            clamp("direction", s, current[s], "neutral call freezes the leg")
        elif call["call"] == "bullish" and target[s] < 0:
            clamp("direction", s, 0.0, "bullish call cannot end short; clamped to flat")
        elif call["call"] == "bearish" and target[s] > 0:
            clamp("direction", s, 0.0, "bearish call cannot end long; clamped to flat")

    # 3. per-commodity cap
    equity = book_state["equity"]
    for s in symbols:
        cap_lots = config.per_commodity_cap * equity / risk[s]["notional_per_lot"]
        if abs(target[s]) > cap_lots:
            clamp("per_commodity_cap", s, cap_lots if target[s] > 0 else -cap_lots, f"notional capped at {cap_lots:g} lots")

    # 7. no-churn threshold
    for s in symbols:
        resize = abs(target[s] - current[s])
        step_lots = specs[s]["step_volume"] / specs[s]["lot_size"]
        threshold = max(config.min_resize_fraction * abs(current[s]), step_lots)
        if s not in forced and 0 < resize < threshold - _EPS:
            clamp("no_churn", s, current[s], "resize below the no-churn threshold")

    # 8. drawdown breaker: reductions only
    if breaker_active:
        for s in symbols:
            same_side = target[s] * current[s] > 0
            reduced = target[s] if same_side and abs(target[s]) <= abs(current[s]) else (current[s] if same_side else 0.0)
            clamp("drawdown_breaker", s, reduced, "drawdown breaker active: reductions only")

    def notional(s: str) -> float:
        return abs(target[s]) * risk[s]["notional_per_lot"]

    def scale(rule: str, legs: list[str], factor: float, detail: str) -> None:
        for s in legs:
            if target[s] != 0:
                clamp(rule, s, target[s] * factor, detail)

    # 4. grain bucket: each direction's notional against the cap
    grains = [s for s in symbols if s in _GRAINS]
    for sign in (1, -1):
        side = [s for s in grains if target[s] * sign > 0]
        total = sum(notional(s) for s in side)
        limit = config.grain_bucket_cap * equity
        if total > limit:
            scale("grain_bucket", side, limit / total, "grain bucket over its notional cap")

    # 5. gross leverage
    gross, limit = sum(notional(s) for s in symbols), config.gross_leverage_cap * equity
    if gross > limit:
        scale("gross_leverage", symbols, limit / gross, "gross notional over its cap")

    # 6. margin buffer
    margin = sum(notional(s) * specs[s]["margin_rate"] for s in symbols)
    limit = config.margin_buffer * equity
    if margin > limit:
        scale("margin_buffer", symbols, limit / margin, "used margin over its share of equity")

    # Rounding last, toward zero on the target position, so it can never lift a leg back over a cap.
    for s in symbols:
        step_lots = specs[s]["step_volume"] / specs[s]["lot_size"]
        min_lots = specs[s]["min_volume"] / specs[s]["lot_size"]
        rounded = int(abs(target[s]) / step_lots + _EPS) * step_lots
        if step_lots - _EPS <= rounded < min_lots - _EPS:
            clamp("min_volume", s, 0.0, "minimum volume exceeds what the limits allow; not rounded up")
            continue
        target[s] = rounded if target[s] >= 0 else -rounded

    return {s: target[s] - current[s] for s in symbols}, violations

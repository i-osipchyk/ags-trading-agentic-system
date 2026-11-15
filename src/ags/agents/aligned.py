"""Adds the common envelope (alignment.py) to the analysts' outputs, in code (ROADMAP.md Step 1).

Runs after the four analysts, on their finished outputs, so the analysts stay blind to each other: nothing here
feeds back into a model call. The evidence fields of each output stay as they are; the envelope is merged next to
them. A crashed analyst (an `error` output) carries no signal and gets no envelope; one whose alignment step
fails keeps its evidence and records `alignment_error`, rather than losing the run.
"""

from datetime import date, datetime

from ags.alignment import (
    PRICE_SCALE,
    WEATHER_SCALE,
    adjust_bias,
    make_envelope,
    news_bias,
    supply_demand_bias,
    technical_bias,
    weather_bias,
)
from ags.tools.pit_store import ET

_NAME = {1: "bullish", 0: "neutral", -1: "bearish"}
_REGION_DIRECTION = {"favourable": "bearish", "watch": "bullish", "severe": "bullish"}
_TREND_DIRECTION = {"up": "bullish", "sideways": "neutral", "down": "bearish"}
_WEEKLY_NOTE = "Mechanical bias from the short, medium and long trend states (+1 up, 0 sideways, -1 down each)."


def _direction(bias: int) -> str:
    return _NAME[(bias > 0) - (bias < 0)]


def _technical(output: dict) -> dict:
    trend = output.get("trend")
    code_bias, confidence = technical_bias(trend)
    bias, reason = adjust_bias(code_bias, output.get("bias_adjustment"), output.get("adjustment_reason"), PRICE_SCALE)
    drivers = [
        {"driver": f"{window} trend {trend[window]['state']} for {trend[window]['age']} sessions",
         "direction": _TREND_DIRECTION[trend[window]["state"]]}
        for window in ("short", "medium", "long")
    ] if trend else []
    if reason:
        drivers.append({"driver": f"analyst adjustment: {reason}", "direction": _direction(bias - code_bias)})
    return make_envelope(
        bias=bias, code_bias=code_bias, scale=PRICE_SCALE, confidence=confidence, horizon="1 week", drivers=drivers,
        invalidation=output.get("invalidation") or [], bias_source="code+model" if reason else "code",
        basis=_WEEKLY_NOTE + " The analyst may move it one step, with a stated reason.", adjustment_reason=reason,
    )


def _news(output: dict, as_of: date) -> dict:
    bias, confidence, basis = news_bias(output, as_of=as_of)
    drivers = [
        {"driver": f"{h['headline']} ({h.get('date')}): {h.get('reason', '')}".strip(), "direction": h.get("direction")}
        for h in sorted(output.get("headlines") or [], key=lambda h: h.get("date") or "", reverse=True)[:3]
        if h.get("direction") in {"bullish", "bearish", "neutral"}
    ]
    return make_envelope(
        bias=bias, scale=PRICE_SCALE, confidence=confidence, horizon="days", drivers=drivers,
        invalidation=output.get("invalidation") or [], bias_source="code", basis=basis,
    )


def _weather(output: dict) -> dict:
    bias, confidence, basis, context = weather_bias(output, stage=output["stage_of_crop_cycle"])
    conditions = output.get("current_conditions") or {}
    drivers = [
        {"driver": f"{region}: {label}. {conditions.get(region, '')}".strip(), "direction": _REGION_DIRECTION[label]}
        for region, label in (output.get("risk_assessment") or {}).items() if label in _REGION_DIRECTION
    ]
    return make_envelope(
        bias=bias, scale=WEATHER_SCALE, confidence=confidence, horizon="weeks", drivers=drivers,
        invalidation=output.get("invalidation") or [], bias_source="code", basis=basis, context=context,
    )


def _supply_demand(output: dict, as_of: date) -> dict:
    bias, confidence, basis = supply_demand_bias(output, as_of=as_of)
    return make_envelope(
        bias=bias, scale=PRICE_SCALE, confidence=confidence, horizon="month",
        drivers=[{"driver": basis, "direction": _direction(bias)}] if output.get("latest_report") else [],
        invalidation=["The next release revises the balance sheet the other way"] if bias else [],
        bias_source="code", basis=basis,
    )


def align_outputs(outputs: dict[str, dict], *, as_of: datetime) -> dict[str, dict]:
    """Each analyst's output with the common envelope merged in (a new dict; the input is left alone)."""
    day = as_of.astimezone(ET).date()
    builders = {
        "technical": lambda o: _technical(o),
        "news": lambda o: _news(o, day),
        "weather": lambda o: _weather(o),
        "supply_demand": lambda o: _supply_demand(o, day),
    }
    aligned = {}
    for analyst, output in outputs.items():
        if analyst not in builders or "error" in (output or {}):
            aligned[analyst] = output
            continue
        try:
            aligned[analyst] = {**output, **builders[analyst](output)}
        except Exception as exc:
            aligned[analyst] = {**output, "alignment_error": f"{type(exc).__name__}: {exc}"}
    return aligned

"""The output every analyst hands the coordinator, and the code that computes the biases (ROADMAP.md Step 1).

Pure functions: no model call, no I/O. Each analyst's own evidence fields stay as they are; this adds a common
envelope next to them, so the coordinator reads one shape from all four.

A `bias` is a signed integer for the PRICE of the commodity (positive = bullish), on the analyst's own `scale`,
which travels inside the envelope together with what each value means, so the coordinator is never left to guess
what a number stands for. Most price biases are -2..+2; the weather bias is -1..+1 because weather is a modifier
of supply, not a trend. Biases are computed here, in code, from facts (trend states, a stocks-to-use revision,
dated headline directions, a crop stage and regional labels); the model only comments, and the technical analyst's
model may move its bias one step with a stated reason, in which case both `code_bias` and `bias` are kept.
"""

from dataclasses import dataclass
from datetime import date

HORIZONS = ("days", "1 week", "weeks", "month")
BIAS_SOURCES = ("code", "code+model")
_DIRECTION = {"bullish": 1, "neutral": 0, "bearish": -1}


@dataclass(frozen=True)
class Scale:
    low: int
    high: int
    meaning: dict[int, str]

    def clamp(self, value: int) -> int:
        return max(self.low, min(self.high, value))

    def as_dict(self) -> dict:
        return {"min": self.low, "max": self.high, "meaning": {str(k): v for k, v in sorted(self.meaning.items())}}


PRICE_SCALE = Scale(-2, 2, {
    -2: "strongly bearish for the price", -1: "mildly bearish", 0: "no directional view",
    1: "mildly bullish", 2: "strongly bullish for the price",
})
WEATHER_SCALE = Scale(-1, 1, {
    -1: "conditions are favourable for the crop at its current stage: more supply, bearish",
    0: "no weather effect on supply: benign, or the crop is not weather-sensitive at this stage",
    1: "conditions threaten supply at the crop's current stage: bullish",
})


def make_envelope(
    *, bias: int, scale: Scale, confidence: int, horizon: str, drivers: list[dict], invalidation: list[str],
    bias_source: str, basis: str, code_bias: int | None = None, adjustment_reason: str | None = None,
    context: dict | None = None,
) -> dict:
    """The common fields. `basis` says in words how the bias was reached and what is already in it."""
    if not scale.low <= bias <= scale.high:
        raise ValueError(f"bias {bias} is outside the scale {scale.low}..{scale.high}")
    if not 1 <= confidence <= 5:
        raise ValueError(f"confidence {confidence} is outside 1..5")
    if horizon not in HORIZONS:
        raise ValueError(f"horizon {horizon!r} is not one of {HORIZONS}")
    if bias_source not in BIAS_SOURCES:
        raise ValueError(f"bias_source {bias_source!r} is not one of {BIAS_SOURCES}")
    envelope = {
        "bias": bias,
        "code_bias": bias if code_bias is None else code_bias,
        "bias_source": bias_source,
        "scale": scale.as_dict(),
        "confidence": confidence,
        "horizon": horizon,
        "drivers": drivers,
        "invalidation": invalidation,
        "basis": basis,
    }
    if adjustment_reason:
        envelope["adjustment_reason"] = adjustment_reason
    if context:
        envelope["context"] = context
    return envelope


def adjust_bias(code_bias: int, step: object, reason: object, scale: Scale) -> tuple[int, str | None]:
    """The model's one-step move of a code bias, kept only with a stated reason. Returns (bias, reason applied)."""
    if step not in (-1, 1) or isinstance(step, bool) or not isinstance(reason, str) or not reason.strip():
        return code_bias, None
    moved = scale.clamp(code_bias + step)
    return (moved, reason.strip()) if moved != code_bias else (code_bias, None)


# --- technical -------------------------------------------------------------------------------------------

_TREND_VOTE = {"up": 1, "sideways": 0, "down": -1}
_TREND_BIAS = {3: 2, 2: 1, 1: 1, 0: 0, -1: -1, -2: -1, -3: -2}


def technical_bias(trend: dict | None) -> tuple[int, int]:
    """(bias, confidence) from the short, medium and long trend states: each votes +1, 0 or -1.

    Three agreeing votes are a strong bias (+/-2), one or two net votes a mild one, a tie none. Confidence is how
    many of the three states agree.
    """
    if not trend:
        return 0, 1
    votes = [_TREND_VOTE[trend[window]["state"]] for window in ("short", "medium", "long")]
    agree = max(votes.count(1), votes.count(0), votes.count(-1))
    return _TREND_BIAS[sum(votes)], {3: 4, 2: 3, 1: 2}[agree]


# --- supply/demand ---------------------------------------------------------------------------------------

STRONG_REVISION = 0.03    # ending stocks revised by 3% or more
MILD_REVISION = 0.005
STRONG_GRIND_YOY = 8.0    # cocoa: average year-on-year grind, percent
MILD_GRIND_YOY = 3.0
FRESH_REPORT_DAYS = 14
CURRENT_REPORT_DAYS = 40


def _report_confidence(report_date: str, as_of: date) -> int:
    age = (as_of - date.fromisoformat(report_date)).days
    return 4 if age <= FRESH_REPORT_DAYS else 3 if age <= CURRENT_REPORT_DAYS else 2


def supply_demand_bias(output: dict, *, as_of: date) -> tuple[int, int, str]:
    """(bias, confidence, one-line basis) from the code-computed supply/demand output.

    A balance sheet tightening (ending stocks revised down) is bullish and a loosening one bearish, strong at a
    revision of 3% or more. Cocoa has no balance sheet: its demand side is the year-on-year grind, strong at an
    average of 8% either way. No revision or no release means no view, at the lowest confidence.
    """
    report = output.get("latest_report")
    if output.get("degraded") or not report:
        return 0, 1, "no usable release this cycle, so no view"
    confidence = _report_confidence(report["date"], as_of)
    grinds = [g["yoy_pct"] for g in (output.get("demand_indicators") or {}).values() if g and g.get("yoy_pct") is not None]
    if grinds:
        mean = sum(grinds) / len(grinds)
        strength = 2 if abs(mean) >= STRONG_GRIND_YOY else 1 if abs(mean) >= MILD_GRIND_YOY else 0
        return (strength if mean > 0 else -strength), confidence, f"average grind {mean:+.1f}% year on year ({report['name']})"
    stocks = next((f for f in output.get("key_figures", []) if f["figure"].startswith("ending_stocks")), None)
    if stocks is None or not stocks.get("prior"):
        return 0, 1, f"{report['name']} gives no revision to the ending stocks, so no view"
    change = (stocks["value"] - stocks["prior"]) / stocks["prior"]
    strength = 2 if abs(change) >= STRONG_REVISION else 1 if abs(change) >= MILD_REVISION else 0
    return (-strength if change > 0 else strength), confidence, (
        f"ending stocks revised {change:+.1%} in the {report['name']} of {report['date']}"
    )


# --- news ------------------------------------------------------------------------------------------------

NEWS_HALF_LIFE_DAYS = 3.0
_NEWS_SHRINK = 1.0        # extra weight on "no view", so a lone or old headline cannot swing the bias
STRONG_NEWS = 0.5
MILD_NEWS = 0.2


def news_bias(output: dict, *, as_of: date) -> tuple[int, int, str]:
    """(bias, confidence, basis) rolling up each headline's direction, newest weighing most.

    A headline's weight halves every 3 days. The weighted mean direction is shrunk toward zero, so a few or old
    headlines give a weak bias. Confidence follows the total weight of evidence, one lower for a carried-forward
    output, which was not refreshed this cycle.
    """
    mass = signal = 0.0
    for item in output.get("headlines") or []:
        direction = _DIRECTION.get(item.get("direction"))
        if direction is None or not item.get("date"):
            continue
        age = max(0, (as_of - date.fromisoformat(item["date"])).days)
        weight = 0.5 ** (age / NEWS_HALF_LIFE_DAYS)
        mass += weight
        signal += weight * direction
    if mass == 0:
        return 0, 1, "no dated headlines, so no view"
    score = signal / (mass + _NEWS_SHRINK)
    strength = 2 if abs(score) >= STRONG_NEWS else 1 if abs(score) >= MILD_NEWS else 0
    confidence = 4 if mass >= 3 else 3 if mass >= 1.5 else 2 if mass >= 0.5 else 1
    if output.get("carried_forward"):
        confidence = max(1, confidence - 1)
    return (strength if score > 0 else -strength), confidence, f"recency-weighted headline direction {score:+.2f}"


# --- weather ---------------------------------------------------------------------------------------------

# How much weather can move the crop at each growth stage (growing_calendar.py): critical stages decide yield,
# relevant ones can still hurt or delay, off-season stages cannot.
_CRITICAL = {"flowering", "grain_fill", "boll_development", "fruit_set"}
_OFF_SEASON = {"dormant"}
STAGE_WEIGHT = {"critical": 1.0, "relevant": 0.5, "off_season": 0.0}
# What a region's label says about supply: a threat is bullish, favourable conditions bearish.
REGION_SCORE = {"favourable": -1.0, "benign": 0.0, "watch": 0.5, "severe": 1.0}
_WEATHER_STEP = 0.5       # a stage-weighted score of 0.5 or more (or -0.5 or less) moves the bias


def stage_sensitivity(stage: str) -> str:
    return "critical" if stage in _CRITICAL else "off_season" if stage in _OFF_SEASON else "relevant"


def weather_bias(output: dict, *, stage: str) -> tuple[int, int, str, dict]:
    """(bias, confidence, basis, context) from the model's regional labels and the crop stage, both in the output.

    Each region's label scores -1 (favourable) to +1 (severe); the mean over the covered regions is multiplied by
    the stage's weight and rounded to -1, 0 or +1. The stage weighting is part of the bias: `context` and `basis`
    state the stage and that it is already applied, so the coordinator must not discount it again.
    """
    sensitivity = stage_sensitivity(stage)
    weight = STAGE_WEIGHT[sensitivity]
    scores = [REGION_SCORE[label] for label in (output.get("risk_assessment") or {}).values() if label in REGION_SCORE]
    context = {"stage_of_crop_cycle": stage, "stage_sensitivity": sensitivity, "stage_weight": weight,
               "stage_already_applied": True}
    stage_note = (
        f"Crop stage is {stage} ({sensitivity.replace('_', '-')}, weight {weight:g}); the stage weighting is "
        "already included in this bias, so do not discount it again."
    )
    if not scores:
        return 0, 1, f"no usable regional assessment, so no view. {stage_note}", context
    mean = sum(scores) / len(scores)
    weighted = mean * weight
    bias = 1 if weighted >= _WEATHER_STEP else -1 if weighted <= -_WEATHER_STEP else 0
    confidence = 2 if sensitivity == "off_season" else (4 if len(scores) >= 3 else 3)
    if output.get("degraded"):
        confidence = min(confidence, 2)
    return bias, confidence, f"mean regional score {mean:+.2f} x stage weight {weight:g} = {weighted:+.2f}. {stage_note}", context


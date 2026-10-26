import json
from datetime import datetime
from typing import Callable

from ags.llm.loop import ChatClient, ToolSpec, run_json_loop

HORIZON = "1 week"

SYSTEM_PROMPT_TEMPLATE = """You are the Coordinator for {commodity} futures.

You synthesize the four analyst outputs below into one call for a fixed
{horizon} horizon, as of {as_of}. You do not re-research: use only these
outputs. Attribute the call to the analysts that drove it and surface any
disagreement explicitly rather than smoothing it over. An output marked
"carried_forward": true was not refreshed this cycle: it is the latest one
from an earlier run (named by "source_run_id"), so weigh its age.

An analyst output with "degraded": true is partial — a source or tool failed, or
its reply came back incomplete — so weigh it as weaker evidence. An output with
an "error" field crashed and carries no signal: list it as neither supporting
nor dissenting. Anything an analyst returned that didn't fit its schema is under
"unparsed_reply": use it as that analyst's evidence, weighed like a degraded output.

Conviction rubric (anchored to analyst agreement, not free judgment):
  5 = all four analysts agree, no material dissent
  4 = three of four agree, dissent is minor/tangential
  3 = split or mixed signals, going with a plurality read
  2 = one or two weak signals against a backdrop of mostly neutral/no-signal analysts
  1 = direction called mostly on priors, not fresh evidence this week

Respond with a JSON object containing exactly these fields:
  "call": "bullish" | "bearish" | "neutral"
  "conviction": integer 1-5 per the rubric
  "supporting_analysts": analysts (technical/news/weather/supply_demand) that agree with the call
  "dissenting_analysts": analysts that disagree
  "thesis": short synthesis text
  "key_drivers": list of {{"driver": ..., "source": the analyst it came from (technical/news/weather/supply_demand)}}
  "invalidation_conditions": list of what would prove the call wrong
No other text — JSON only.

If the read_track_record tool is available, you may call it to review your own
past calls for {commodity} and how they graded out. Its data is variable-shaped:
each past run lists only the audit checkpoints (1/2/4/8 weeks) that have been
graded so far, so a run with no audits simply has not been graded yet — that is
not a miss. Use it to weigh drivers that did or didn't play out, not to
override this week's analyst evidence."""


def _rubric_violations(conviction: int, supporting: list, dissenting: list, analyst_outputs: dict) -> list[str]:
    violations = []
    # Only the agreement-anchored rungs are checkable from the output alone:
    # 5 needs unanimous support, 4 needs at least three of four.
    if conviction == 5 and (len(supporting) != 4 or dissenting):
        violations.append(f"conviction 5 requires all four analysts supporting with no dissent; got {len(supporting)} supporting, {len(dissenting)} dissenting")
    if conviction == 4 and len(supporting) < 3:
        violations.append(f"conviction 4 requires at least three analysts supporting; got {len(supporting)}")
    crashed = sorted(a for a in {*supporting, *dissenting} if a in _crashed(analyst_outputs))
    if crashed:
        violations.append(f"crashed analysts carry no signal but were counted: {', '.join(crashed)}")
    return violations


def _crashed(analyst_outputs: dict) -> set[str]:
    return {a for a, output in analyst_outputs.items() if "error" in (output or {})}


def _capped_conviction(conviction: int, supporting: list, dissenting: list) -> int:
    """The highest conviction the agreement-anchored rungs allow, so a rubric breach can't size up a leg."""
    if conviction == 5 and (len(supporting) != 4 or dissenting):
        conviction = 4
    if conviction == 4 and len(supporting) < 3:
        conviction = 3
    return conviction


_CALLS = {"bullish", "bearish", "neutral"}
_ANALYSTS = {"technical", "news", "weather", "supply_demand"}


def _analyst_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(n, str) and n in _ANALYSTS for n in value)


def _text_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _driver_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(d, dict) and "driver" in d and isinstance(d.get("source"), str) and d["source"] in _ANALYSTS
        for d in value
    )


_ANALYST_NAMES = "/".join(sorted(_ANALYSTS))
_FIELD_RULES = {
    "call": (lambda v: isinstance(v, str) and v in _CALLS, "must be one of bullish/bearish/neutral"),
    "conviction": (
        lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5,
        "must be an integer from 1 to 5",
    ),
    "supporting_analysts": (_analyst_list, f"must be a list of analyst names ({_ANALYST_NAMES})"),
    "dissenting_analysts": (_analyst_list, f"must be a list of analyst names ({_ANALYST_NAMES})"),
    "thesis": (lambda v: isinstance(v, str), "must be text"),
    "key_drivers": (_driver_list, f"must be a list of {{driver, source}}, source one of {_ANALYST_NAMES}"),
    "invalidation_conditions": (_text_list, "must be a list of text"),
}
_FIELDS = tuple(_FIELD_RULES)
# Without these there is no call to size; every other field falls back to empty on its own.
_CORE_FIELDS = {"call", "conviction"}


def _invalid_fields(parsed: dict) -> dict[str, str]:
    """Why each unusable field is wrong; empty when the reply fits the schema."""
    invalid = {}
    for field, (valid, rule) in _FIELD_RULES.items():
        if field not in parsed:
            invalid[field] = "missing"
        elif not valid(parsed[field]):
            invalid[field] = rule
    if not {"supporting_analysts", "dissenting_analysts"} & invalid.keys():
        both = sorted(set(parsed["supporting_analysts"]) & set(parsed["dissenting_analysts"]))
        if both:
            for field in ("supporting_analysts", "dissenting_analysts"):
                invalid[field] = f"an analyst can't both support and dissent: {', '.join(both)}"
    return invalid


def _reply_problems(parsed: object, analyst_outputs: dict) -> list[str]:
    """What to send back for the one retry: schema problems, then rubric breaches."""
    if not isinstance(parsed, dict):
        return ["the reply must be a JSON object"]
    invalid = _invalid_fields(parsed)
    problems = [f"{field}: {why}" for field, why in invalid.items()]
    if not {"conviction", "supporting_analysts", "dissenting_analysts"} & invalid.keys():
        problems += _rubric_violations(
            parsed["conviction"], parsed["supporting_analysts"], parsed["dissenting_analysts"], analyst_outputs
        )
    return problems


def _result(price_at_call: dict | None, *, degraded: bool = False, **fields) -> dict:
    return {
        "call": None,
        "conviction": None,
        "horizon": HORIZON,
        "price_at_call": price_at_call,
        "supporting_analysts": [],
        "dissenting_analysts": [],
        "thesis": None,
        "key_drivers": [],
        "invalidation_conditions": [],
        "rubric_violations": [],
        **fields,
        "degraded": degraded,
    }


def run_coordinator(
    chat_client: ChatClient,
    *,
    commodity: str,
    as_of: datetime,
    model: str,
    analyst_outputs: dict,
    price_at_call: dict | None = None,
    track_record: Callable[[int], list[dict]] | None = None,
) -> dict:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(commodity=commodity, as_of=as_of.isoformat(), horizon=HORIZON)
    user_prompt = (
        f"Analyst outputs for {commodity} as of {as_of.isoformat()}:\n"
        + json.dumps(analyst_outputs, indent=2, default=str)
    )

    tools = []
    if track_record is not None:
        tools.append(
            ToolSpec(
                name="read_track_record",
                description="Your own past calls for this commodity, each with whichever audit checkpoints have been graded.",
                parameters={
                    "type": "object",
                    "properties": {
                        "lookback_weeks": {"type": "integer", "description": "How many weeks back to read."}
                    },
                    "required": ["lookback_weeks"],
                },
                function=lambda lookback_weeks: {"runs": track_record(lookback_weeks)},
            )
        )

    loop_result = run_json_loop(
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools,
        validate=lambda parsed: _reply_problems(parsed, analyst_outputs),
    )
    try:
        parsed = json.loads(loop_result.content)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        # A malformed call degrades the run rather than crashing the pipeline,
        # same as the analysts; the null call is still logged and scoreable.
        return _result(price_at_call, degraded=True)
    invalid = _invalid_fields(parsed)
    if _CORE_FIELDS & invalid.keys():
        return _result(price_at_call, degraded=True, invalid_fields=invalid)

    # Past the retry, a bad non-core field falls back to empty and degrades the call instead of voiding it.
    valid = {field: parsed[field] for field in _FIELDS if field not in invalid}
    supporting = valid.get("supporting_analysts", [])
    dissenting = valid.get("dissenting_analysts", [])
    violations = _rubric_violations(parsed["conviction"], supporting, dissenting, analyst_outputs)
    # A crashed analyst carries no signal, so it neither supports nor dissents, whatever the reply says.
    crashed = _crashed(analyst_outputs)
    supporting = [a for a in supporting if a not in crashed]
    dissenting = [a for a in dissenting if a not in crashed]
    conviction = _capped_conviction(parsed["conviction"], supporting, dissenting)

    return _result(
        price_at_call,
        **{field: valid[field] for field in ("call", "thesis", "key_drivers", "invalidation_conditions") if field in valid},
        conviction=conviction,
        supporting_analysts=supporting,
        dissenting_analysts=dissenting,
        rubric_violations=violations,
        **({"conviction_as_proposed": parsed["conviction"]} if conviction != parsed["conviction"] else {}),
        **({"invalid_fields": invalid} if invalid else {}),
        # A failed read_track_record call means the call was made on less than it asked for.
        degraded=bool(invalid) or loop_result.tool_errors > 0,
    )

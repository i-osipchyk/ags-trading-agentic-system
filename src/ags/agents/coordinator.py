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
nor dissenting.

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
  "key_drivers": list of {{"driver": ..., "source": the analyst it came from}}
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
    crashed = sorted(a for a in {*supporting, *dissenting} if "error" in (analyst_outputs.get(a) or {}))
    if crashed:
        violations.append(f"crashed analysts carry no signal but were counted: {', '.join(crashed)}")
    return violations


_CALLS = {"bullish", "bearish", "neutral"}
_ANALYSTS = {"technical", "news", "weather", "supply_demand"}
_FIELDS = (
    "call",
    "conviction",
    "supporting_analysts",
    "dissenting_analysts",
    "thesis",
    "key_drivers",
    "invalidation_conditions",
)


def _validate(parsed: dict) -> None:
    for field in _FIELDS:
        if field not in parsed:
            raise ValueError(f"missing field: {field}")
    if parsed["call"] not in _CALLS:
        raise ValueError(f"unknown call: {parsed['call']!r}")
    conviction = parsed["conviction"]
    if isinstance(conviction, bool) or not isinstance(conviction, int) or not 1 <= conviction <= 5:
        raise ValueError(f"conviction out of range: {conviction!r}")
    for field in ("supporting_analysts", "dissenting_analysts"):
        names = parsed[field]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or not set(names) <= _ANALYSTS:
            raise ValueError(f"{field} must list analysts from {sorted(_ANALYSTS)}: {names!r}")
    if not isinstance(parsed["thesis"], str):
        raise ValueError("thesis must be text")
    drivers = parsed["key_drivers"]
    if not isinstance(drivers, list) or not all(isinstance(d, dict) and "driver" in d and "source" in d for d in drivers):
        raise ValueError("key_drivers must be a list of {driver, source}")
    conditions = parsed["invalidation_conditions"]
    if not isinstance(conditions, list) or not all(isinstance(c, str) for c in conditions):
        raise ValueError("invalidation_conditions must be a list of text")


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
        chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=tools
    )
    try:
        parsed = json.loads(loop_result.content)
        if not isinstance(parsed, dict):
            raise ValueError("reply is not a JSON object")
        _validate(parsed)
    except (json.JSONDecodeError, ValueError):
        # A malformed call degrades the run rather than crashing the pipeline,
        # same as the analysts; the null call is still logged and scoreable.
        return _result(price_at_call, degraded=True)

    return _result(
        price_at_call,
        call=parsed["call"],
        conviction=parsed["conviction"],
        supporting_analysts=parsed["supporting_analysts"],
        dissenting_analysts=parsed["dissenting_analysts"],
        thesis=parsed["thesis"],
        key_drivers=parsed["key_drivers"],
        invalidation_conditions=parsed["invalidation_conditions"],
        rubric_violations=_rubric_violations(
            parsed["conviction"], parsed["supporting_analysts"], parsed["dissenting_analysts"], analyst_outputs
        ),
        # The call stands, but a failed read_track_record call means it was made on less than it asked for.
        degraded=loop_result.tool_errors > 0,
    )

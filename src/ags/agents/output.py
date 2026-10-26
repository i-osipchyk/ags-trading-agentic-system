"""Shape checks for an agent's final JSON reply, at the handoff to the next stage.

Top-level only: each field's JSON type, and for lists the type of their items. Deeper
content (enum values, nested keys) stays the model's job and is graded by the auditor.

What doesn't fit is kept, not dropped: it goes to the coordinator under `unparsed_reply`,
so a reply that's slightly off still contributes what it has.
"""

import json
from collections.abc import Callable

Check = Callable[[object], bool]


def of(*types: type) -> Check:
    """Any of these JSON types (pass `type(None)` for a field that may be null)."""
    return lambda value: isinstance(value, types)


def list_of(item_type: type) -> Check:
    return lambda value: isinstance(value, list) and all(isinstance(item, item_type) for item in value)


def parse_reply(content: str, fields: dict[str, Check]) -> tuple[dict, bool, dict]:
    """The fields that came back in the expected shape, whether every one of them did, and the rest.

    A missing or mistyped field is left out of the first so the caller falls back to its default
    for it. The third is `{"unparsed_reply": ...}` to merge into the output: the mistyped and
    unexpected keys of a JSON object, or the whole reply when it isn't one. It's empty when
    nothing was left over.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}, False, {"unparsed_reply": content} if content.strip() else {}
    if not isinstance(parsed, dict):
        return {}, False, {"unparsed_reply": parsed}
    valid = {name: parsed[name] for name, check in fields.items() if name in parsed and check(parsed[name])}
    rest = {name: value for name, value in parsed.items() if name not in valid}
    return valid, len(valid) == len(fields), {"unparsed_reply": rest} if rest else {}

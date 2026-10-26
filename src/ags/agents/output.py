"""Shape checks for an agent's final JSON reply, at the handoff to the next stage.

Top-level only: each field's JSON type, and for lists the type of their items. Deeper
content (enum values, nested keys) stays the model's job and is graded by the auditor.
"""

import json
from collections.abc import Callable

Check = Callable[[object], bool]


def of(*types: type) -> Check:
    """Any of these JSON types (pass `type(None)` for a field that may be null)."""
    return lambda value: isinstance(value, types)


def list_of(item_type: type) -> Check:
    return lambda value: isinstance(value, list) and all(isinstance(item, item_type) for item in value)


def parse_reply(content: str, fields: dict[str, Check]) -> tuple[dict, bool]:
    """The fields that came back in the expected shape, and whether every one of them did.

    A missing or mistyped field is left out so the caller falls back to its default for it;
    a reply that isn't a JSON object yields no fields at all.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}, False
    if not isinstance(parsed, dict):
        return {}, False
    valid = {name: parsed[name] for name, check in fields.items() if name in parsed and check(parsed[name])}
    return valid, len(valid) == len(fields)

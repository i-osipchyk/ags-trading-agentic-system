def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _scalar(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _inline(mapping: dict) -> str:
    return " · ".join(f"**{_label(k)}:** {_scalar(v)}" for k, v in mapping.items())


def _lines(mapping: dict, depth: int) -> list[str]:
    pad = "  " * depth
    lines = []
    for key, value in mapping.items():
        label = f"{pad}- **{_label(key)}:**"
        if isinstance(value, dict) and value:
            lines.append(label)
            lines.extend(_lines(value, depth + 1))
        elif isinstance(value, list) and value and any(isinstance(v, (dict, list)) for v in value):
            lines.append(label)
            lines.extend(
                f"{pad}  - {_inline(v) if isinstance(v, dict) else _scalar(v)}" for v in value
            )
        elif isinstance(value, list):
            lines.append(f"{label} {', '.join(_scalar(v) for v in value) if value else 'none'}")
        else:
            lines.append(f"{label} {_scalar(value) if value != {} else 'none'}")
    return lines


def to_markdown(output: dict) -> str:
    """Render a structured agent output as readable markdown bullets, never raw JSON."""
    return "\n".join(_lines(output, 0))

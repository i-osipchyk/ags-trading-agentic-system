import difflib
import json

from ags.replay.coordinator import run_time
from ags.replay.plan import cycle_time
from ags.replay.spec import load_spec
from ags.storage import Prefixed, Storage


def _first_prompt(log_store: Storage, run_key: str, agent: str) -> dict | None:
    """The model and system prompt of `agent`'s first request in a log, as actually sent."""
    data = log_store.get(f"{run_key}.jsonl")
    for line in (data or b"").decode().splitlines():
        event = json.loads(line)
        if event["agent"] == agent and event["event_type"] == "prompt":
            messages = event["payload"]["messages"]
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            return {"model": event["payload"]["model"], "system": system}
    return None


def _latest(log_store: Storage, agent: str, when) -> str | None:
    """The newest run in a log folder that has a prompt from `agent`."""
    keys = [k.removesuffix(".jsonl") for k in log_store.list("") if "/" not in k and k.endswith(".jsonl")]
    for key in sorted(keys, key=lambda k: (when(k), k), reverse=True):
        if _first_prompt(log_store, key, agent) is not None:
            return key
    return None


def _section(title: str, agent: str, official: Storage, replayed: Storage, when) -> list[str]:
    lines = [f"## {title}", ""]
    key = _latest(replayed, agent, when)
    if key is None:
        return [*lines, "Not replayed yet.", ""]
    ours, theirs = _first_prompt(replayed, key, agent), _first_prompt(official, key, agent)
    lines.append(f"Compared on `{key}` (the latest replayed run) against the official run at the same time.")
    lines.append("")
    if theirs is None:
        return [*lines, "The official run has no logged prompt to compare with.", ""]
    if ours["model"] == theirs["model"]:
        lines.append(f"Model: {ours['model']} (unchanged)")
    else:
        lines.append(f"Model: {theirs['model']} → {ours['model']}")
    diff = list(difflib.unified_diff(
        theirs["system"].splitlines(), ours["system"].splitlines(), "official", "replay", lineterm="",
    ))
    if not diff:
        return [*lines, "System prompt: no differences", ""]
    return [*lines, "System prompt:", "", "```diff", *diff, "```", ""]


def describe_differences(store: Storage, replay_id: str, *, code_version: str | None = None) -> str:
    """Markdown: how this replay's coordinator and manager differ from the official (deployed) ones.

    Compares what was actually sent to the model on the same run: the model and the system
    prompt (which also carries the output format). Same run, so the date and commodity filled
    into the prompt match and only real changes show. User prompts carry the run's data, not
    the variant, so they are not compared.
    """
    spec = load_spec(store, replay_id)
    replay = Prefixed(store, f"replays/{replay_id}")
    lines = [
        f"# Replay `{replay_id}`",
        "",
        f"- Fork: {spec.fork_at.isoformat()}",
        f"- Coordinator: variant `{spec.coordinator.variant}`, model `{spec.coordinator.model}`",
        f"- Manager: variant `{spec.manager.variant}`, model `{spec.manager.model}`",
    ]
    if code_version:
        lines.append(f"- Code: {code_version}")
    lines.append("")
    lines += _section("Coordinator", "coordinator", Prefixed(store, "logs"), Prefixed(replay, "logs"), run_time)
    lines += _section("Manager", "manager", Prefixed(store, "portfolio/logs"), Prefixed(replay, "portfolio/logs"), cycle_time)
    return "\n".join(lines)

import json
from pathlib import Path

from ags.llm.loop import ChatClient
from ags.logging.run_log import RunLog

_SYSTEM = """You are answering questions about one completed, frozen research run.
Use only the logged analyst outputs and coordinator call below. You have no
tools and no fresh data; if the run record doesn't answer the question, say so.

Run record:
{record}"""


class UnknownRun(Exception):
    pass


def answer_question(
    log_dir: Path,
    run_id: str,
    question: str,
    *,
    chat_client: ChatClient,
    model: str,
    history: list[dict] | None = None,
) -> str:
    if not (Path(log_dir) / f"{run_id}.jsonl").exists():
        raise UnknownRun(run_id)
    events = RunLog(log_dir, run_id).read()
    record = {e["agent"]: e["payload"] for e in events if e["event_type"] == "output"}
    messages = [
        {"role": "system", "content": _SYSTEM.format(record=json.dumps(record, indent=2))},
        *(
            {"role": m["role"], "content": m["content"]}
            for m in history or []
            if m.get("role") in ("user", "assistant")
        ),
        {"role": "user", "content": question},
    ]
    return chat_client.complete(messages, None, model=model)["content"]

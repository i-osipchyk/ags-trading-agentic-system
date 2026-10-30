import json
from datetime import datetime

from ags import blockers
from ags.handlers.runtime import Runtime, check_late_start
from ags.logging import attempts
from ags.logging.run_id import generate_run_id
from ags.logging.run_log import RunLog
from ags.pipeline import run_pipeline
from ags.storage import Prefixed


def _events(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode().splitlines()]


def _reusable_call(events: list[dict]) -> dict | None:
    """The earlier attempt's call, if it made one and no provider blocked it."""
    if any(e["event_type"] == "blocked" for e in events):
        return None
    outputs = [e["payload"] for e in events if e["agent"] == "coordinator" and e["event_type"] == "output"]
    return outputs[-1] if outputs and outputs[-1].get("call") else None


def handle(event: dict, runtime: Runtime) -> dict:
    commodity = event["commodity"]
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    run_id = generate_run_id(commodity, scheduled)
    log_key = f"logs/{run_id}.jsonl"
    # A log already there means this slot was attempted: this is a retry, allowed the retry window.
    earlier = runtime.storage.get(log_key)
    check_late_start(event, runtime.now(), retry_window=runtime.retry_window if earlier is not None else None)
    if earlier is not None:
        call = _reusable_call(_events(earlier))
        if call is not None:
            # Made on time and unblocked: the retry keeps it rather than asking the models again.
            return {"commodity": commodity, "failed": False, "call": call, "blocked": {}, "reused": True}
        attempts.archive(runtime.storage, log_key)
    result = run_pipeline(
        commodity=commodity, as_of=scheduled, trigger_timestamp=scheduled,
        chat_client=runtime.chat_client, model=runtime.model,
        log_dir=Prefixed(runtime.storage, "logs"), data_dir=Prefixed(runtime.storage, "data"),
        audit_dir=Prefixed(runtime.storage, "audits"), prices_client=runtime.market_client,
        news_client=runtime.news_client, weather_client=runtime.weather_client, usda_client=runtime.usda_client,
        cot_client=runtime.cot_client, replace_log=earlier is not None,
    )
    blocked = blockers.seen()
    if blocked:
        # Marks the run as not reusable, so a retry re-runs it once the provider is fixed.
        with RunLog(Prefixed(runtime.storage, "logs"), run_id) as log:
            log.append(agent="pipeline", event_type="blocked", payload={"providers": blocked})
    call = result["call"]
    return {
        "commodity": commodity, "failed": not call.get("call"), "call": call if call.get("call") else None,
        "blocked": blocked,
    }


def handler(event, context):
    from ags.handlers.entry import build_default_runtime, invoke

    return invoke(handle, event, build=build_default_runtime)

from datetime import datetime

from ags.handlers.runtime import Runtime, check_late_start
from ags.pipeline import run_pipeline
from ags.storage import Prefixed


def handle(event: dict, runtime: Runtime) -> dict:
    check_late_start(event, runtime.now())
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    result = run_pipeline(
        commodity=event["commodity"], as_of=scheduled, trigger_timestamp=scheduled,
        chat_client=runtime.chat_client, model=runtime.model,
        log_dir=Prefixed(runtime.storage, "logs"), data_dir=Prefixed(runtime.storage, "data"),
        audit_dir=Prefixed(runtime.storage, "audits"), prices_client=runtime.market_client,
        news_client=runtime.news_client, weather_client=runtime.weather_client, usda_client=runtime.usda_client,
    )
    call = result["call"]
    return {"commodity": event["commodity"], "failed": not call.get("call"), "call": call if call.get("call") else None}


def handler(event, context):
    from ags.handlers.entry import build_default_runtime, invoke

    return invoke(handle, event, build=build_default_runtime)

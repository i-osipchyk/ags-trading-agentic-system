from ags.agents.auditor import run_auditor
from ags.handlers.runtime import Runtime
from ags.storage import Prefixed


def handle(event: dict, runtime: Runtime) -> dict:
    result = run_auditor(
        runtime.chat_client, log_dir=Prefixed(runtime.storage, "logs"), audit_dir=Prefixed(runtime.storage, "audits"),
        data_dir=Prefixed(runtime.storage, "data"), model=runtime.model, now=runtime.now(),
        prices_client=runtime.market_client,
    )
    graded = len(result["graded"]) + len(result.get("graded_diagnostics", []))
    failed = len(result["failed"]) + len(result.get("failed_diagnostics", []))
    return {"graded": graded, "failed": failed}


def handler(event, context):
    from ags.handlers.entry import build_default_runtime, invoke

    return invoke(handle, event, build=build_default_runtime)

from datetime import datetime

from ags.handlers.runtime import Runtime, check_late_start, cycle_id_for
from ags.notify.alerts import breaker_newly_tripped, consecutive_failures
from ags.portfolio.stage import run_portfolio


def handle(event: dict, runtime: Runtime) -> dict:
    now = runtime.now()
    cycle_id = event.get("cycle_id") or cycle_id_for(event["scheduled_time"])
    check_late_start(event, now)
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    # A failed commodity arrives as call=None; the stage turns that into a held leg (guardrail 9).
    calls = {r["commodity"]: r["call"] for r in event["results"]}
    cycle = run_portfolio(
        cycle_id, calls, runtime.storage, as_of=scheduled, now=now, chat_client=runtime.chat_client,
        model=runtime.model, market_client=runtime.market_client, config=runtime.portfolio_config,
        breaker_reset_at=runtime.breaker_reset_at, off_schedule=event.get("off_schedule", False),
    )
    if not event.get("off_schedule", False):
        failing = consecutive_failures(runtime.storage)
        if failing:
            runtime.alert(f"Commodity pipeline failed two cycles running: {', '.join(failing)}")
        if breaker_newly_tripped(runtime.storage):
            runtime.alert(f"Drawdown breaker tripped in {cycle_id}: reductions only until reset")
    return {
        "cycle_id": cycle_id,
        "failed_commodities": [r["commodity"] for r in event["results"] if r["failed"]],
        "manager_equity": cycle["manager"]["equity"],
        "shadow_equity": cycle["shadow"]["equity"],
        "breaker_active": cycle["manager"]["breaker_active"],
    }


def handler(event, context):
    from ags.handlers.entry import build_default_runtime, invoke

    return invoke(handle, event, build=build_default_runtime)

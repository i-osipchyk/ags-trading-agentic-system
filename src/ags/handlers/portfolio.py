import logging
from datetime import datetime

from ags import blockers
from ags.handlers.runtime import Runtime, check_late_start, cycle_id_for
from ags.logging import attempts
from ags.logging.run_id import generate_run_id
from ags.notify.alerts import breaker_newly_tripped, consecutive_failures
from ags.notify.balances import credit_alerts
from ags.notify.spend import cycle_usage
from ags.portfolio.stage import MANAGER_ATTEMPTS, run_portfolio
from ags.tools.pit_store import ET

logger = logging.getLogger(__name__)

_PROVIDER_NAMES = {"deepseek": "DeepSeek", "tavily": "Tavily"}


def held_alert(cycle_id: str, scheduled: datetime, retry_until: datetime, blocked: dict[str, dict[str, str]]) -> str:
    """`blocked`: provider -> {where it hit ("corn", "manager", ...) -> detail}."""
    lines = [f"⛔ {cycle_id} held: not written to either paper book, because a provider is out of credit or "
             "rejecting the key. The next cycle will fail the same way until it's fixed."]
    for provider, where in sorted(blocked.items()):
        detail = next(iter(where.values()))
        lines.append(f"• {_PROVIDER_NAMES.get(provider, provider)}: {detail} ({', '.join(sorted(where))})")
    lines.append(
        f"Fix it, then before {retry_until.astimezone(ET):%a %H:%M} ET run:\n"
        "uv run python scripts/retry_cycle.py\n"
        "Commodities that already have an unblocked call are reused, not re-run."
    )
    return "\n".join(lines)


def _check_credit(runtime: Runtime, event: dict, cycle_id: str) -> None:
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    keys = [f"logs/{generate_run_id(r['commodity'], scheduled)}.jsonl" for r in event["results"]]
    usage = cycle_usage(runtime.storage, [*keys, f"portfolio/logs/{cycle_id}.jsonl"])
    for message in credit_alerts(
        runtime.balances(), cycle_cost_usd=usage["cost_usd"], tavily_credits_per_evening=usage["tavily_credits"],
        min_cycles=runtime.min_cycles_of_credit,
    ):
        runtime.alert(message)


def handle(event: dict, runtime: Runtime) -> dict:
    now = runtime.now()
    cycle_id = event.get("cycle_id") or cycle_id_for(event["scheduled_time"])
    off_schedule = event.get("off_schedule", False)
    log_key = f"portfolio/logs/{cycle_id}.jsonl"
    recorded = runtime.storage.get(f"portfolio/manager/cycles/{cycle_id}.json") is not None
    # Attempted before but not in the book: a held cycle being retried, allowed the retry window.
    retrying = not off_schedule and not recorded and runtime.storage.get(log_key) is not None
    check_late_start(event, now, retry_window=runtime.retry_window if retrying else None)
    if recorded and not off_schedule:
        # The slot is already in the book (one writer, one record per slot): a retry has nothing to do.
        return {"cycle_id": cycle_id, "already_recorded": True}
    if retrying:
        attempts.archive(runtime.storage, log_key)
    scheduled = datetime.fromisoformat(event["scheduled_time"])
    # A failed commodity arrives as call=None; the stage turns that into a held leg (guardrail 9).
    calls = {r["commodity"]: r["call"] for r in event["results"]}
    blocked: dict[str, dict[str, str]] = {}
    for r in event["results"]:
        for provider, detail in (r.get("blocked") or {}).items():
            blocked.setdefault(provider, {})[r["commodity"]] = detail

    def hold_books() -> bool:
        for provider, detail in blockers.seen().items():  # the manager's own calls
            blocked.setdefault(provider, {})["manager"] = detail
        return bool(blocked)

    cycle = run_portfolio(
        cycle_id, calls, runtime.storage, as_of=scheduled, now=now, chat_client=runtime.chat_client,
        model=runtime.model, market_client=runtime.market_client, config=runtime.portfolio_config,
        breaker_reset_at=runtime.breaker_reset_at, off_schedule=off_schedule, hold_books=hold_books,
        replace_log=retrying,
    )
    if not off_schedule:
        if cycle["held"]:
            # The manager failing and commodities failing are this same problem: one alert, not three.
            runtime.alert(held_alert(cycle_id, scheduled, scheduled + runtime.retry_window, blocked))
        else:
            if cycle["manager"]["manager_error"]:
                runtime.alert(
                    f"Portfolio manager failed in {cycle_id} after {MANAGER_ATTEMPTS} attempts, every manager leg held: "
                    f"{cycle['manager']['manager_error']}"
                )
            failing = consecutive_failures(runtime.storage)
            if failing:
                runtime.alert(f"Commodity pipeline failed two cycles running: {', '.join(failing)}")
            if breaker_newly_tripped(runtime.storage):
                runtime.alert(f"Drawdown breaker tripped in {cycle_id}: reductions only until reset")
        try:
            _check_credit(runtime, event, cycle_id)
        except Exception as exc:  # a failed check must not fail a cycle that's already done
            logger.warning("credit check failed for %s: %s", cycle_id, exc)
    return {
        "cycle_id": cycle_id,
        "held": cycle["held"],
        "failed_commodities": [r["commodity"] for r in event["results"] if r["failed"]],
        "manager_equity": cycle["manager"]["equity"],
        "shadow_equity": cycle["shadow"]["equity"],
        "breaker_active": cycle["manager"]["breaker_active"],
    }


def handler(event, context):
    from ags.handlers.entry import build_default_runtime, invoke

    return invoke(handle, event, build=build_default_runtime)

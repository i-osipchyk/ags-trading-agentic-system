import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ags.llm.client import trace
from ags.llm.loop import ChatClient
from ags.logging.run_log import RunLog
from ags.pipeline import LoggingChatClient
from ags.portfolio.guardrails import GuardrailConfig, apply_guardrails
from ags.portfolio.manager import run_portfolio_manager
from ags.portfolio.paper_book import PaperBook
from ags.portfolio.risk import risk_facts
from ags.portfolio.shadow import shadow_proposal
from ags.storage import LocalStorage, Prefixed, Storage
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, get_h1_bars, get_prices, get_quote, get_symbol_specs

COMMODITIES = list(CTRADER_SYMBOL_NAMES)
_DAILY_LOOKBACK_DAYS = 45


@dataclass(frozen=True)
class PortfolioConfig:
    guardrails: GuardrailConfig
    starting_equity: float
    stop_out_level: float
    shadow_risk_budget: float
    drawdown_breaker: float

    @classmethod
    def from_mapping(cls, env) -> "PortfolioConfig":
        """Limits come from config (env vars set by Terraform), defaults per ARCHITECTURE.md."""

        def num(key: str, default: float) -> float:
            return float(env.get(key) or default)

        return cls(
            guardrails=GuardrailConfig(
                per_commodity_cap=num("PER_COMMODITY_CAP", 1.0), grain_bucket_cap=num("GRAIN_BUCKET_CAP", 1.5),
                gross_leverage_cap=num("GROSS_LEVERAGE_CAP", 3.0), margin_buffer=num("MARGIN_BUFFER", 0.40),
                min_resize_fraction=num("MIN_RESIZE_FRACTION", 0.20),
            ),
            starting_equity=num("STARTING_EQUITY_USD", 10_000), stop_out_level=num("STOP_OUT_MARGIN_LEVEL", 0.50),
            shadow_risk_budget=num("SHADOW_RISK_BUDGET", 0.01), drawdown_breaker=num("DRAWDOWN_BREAKER", 0.60),
        )


def _lots(state: dict, specs: dict) -> dict[str, float]:
    lots = {c: 0.0 for c in COMMODITIES}
    for p in state["positions"]:
        sign = 1 if p["trade_side"] == "BUY" else -1
        lots[p["symbol_name"]] = sign * p["volume"] / specs[p["symbol_name"]]["lot_size"]
    return lots


def _last_cycle(storage: Storage, book: str, cycle_id: str) -> dict | None:
    """The latest cycle file strictly before `cycle_id` (cycle ids sort chronologically)."""
    own = f"portfolio/{book}/cycles/{cycle_id}.json"
    earlier = [k for k in storage.list(f"portfolio/{book}/cycles/") if k < own]
    return json.loads(storage.get(earlier[-1])) if earlier else None


def _breaker(prev, snapshot, book, reset_at, config) -> tuple[bool, str | None]:
    """Sticky drawdown breaker. A newer manual reset clears it and rebases the peak to current equity."""
    active = bool(prev and prev.get("breaker_active"))
    applied = prev.get("breaker_reset_applied") if prev else None
    if reset_at is not None and (applied is None or reset_at > datetime.fromisoformat(applied)):
        book.rebase_peak(snapshot["equity"])
        return False, reset_at.isoformat()
    if snapshot["equity"] < config.drawdown_breaker * snapshot["peak_equity"]:
        active = True
    return active, applied


def run_portfolio(
    cycle_id: str,
    calls: dict,
    storage: Storage,
    *,
    as_of: datetime,
    now: datetime | None = None,
    chat_client: ChatClient,
    model: str,
    market_client,
    config: PortfolioConfig,
    breaker_reset_at=lambda: None,
    off_schedule: bool = False,
    data: Storage | None = None,
    quoted_at: datetime | None = None,
    manager=run_portfolio_manager,
    hold_on_manager_error: bool = True,
) -> dict:
    """One portfolio cycle. Live by default; a counterfactual replay passes `data` (the official
    market-data store, read-only) and `quoted_at` (the official cycle's capture instant), so the
    cycle reads back exactly the market data the official one captured and never fetches any,
    and may swap in another `manager` (same signature as `run_portfolio_manager`). With
    `hold_on_manager_error=False` a failed manager call raises instead of holding every leg, so
    a replay is retried rather than recording an outage as the manager's decision.
    """
    # Same upload-once-in-a-finally treatment as a commodity run's log on non-local storage.
    log = RunLog(storage, f"portfolio/logs/{cycle_id}", buffered=not isinstance(storage, LocalStorage))
    try:
        with trace(chat_client, cycle_id):
            return _run_portfolio(
                cycle_id, calls, storage, log, as_of=as_of, now=now, chat_client=chat_client, model=model,
                market_client=market_client, config=config, breaker_reset_at=breaker_reset_at,
                off_schedule=off_schedule, data=data, quoted_at=quoted_at, manager=manager,
                hold_on_manager_error=hold_on_manager_error,
            )
    finally:
        log.close()


def _run_portfolio(
    cycle_id, calls, storage, log, *, as_of, now, chat_client, model, market_client, config, breaker_reset_at, off_schedule,
    data, quoted_at, manager, hold_on_manager_error,
) -> dict:
    now = now or datetime.now(timezone.utc)
    replaying = quoted_at is not None
    data = data if data is not None else Prefixed(storage, "data")
    quoted_at = quoted_at or now
    timestamp = as_of.isoformat()
    calls = {c: (calls.get(c) if (calls.get(c) or {}).get("call") else None) for c in COMMODITIES}

    # Decision inputs (specs, daily bars -> risk facts) are as of the scheduled time. Execution
    # data is captured live at `now`, the instant the stage runs: a fill can only happen at a
    # price available then, and on AWS `as_of` (the scheduled time) is already minutes old. The
    # captures are stored PIT under `now`, so a replay of this cycle reads the same quotes.
    specs, quotes, risk = {}, {}, {}
    for c in COMMODITIES:
        specs[c] = get_symbol_specs(data, commodity=c, as_of=as_of, client=market_client, now=now)
        quotes[c] = get_quote(data, commodity=c, as_of=quoted_at, client=market_client, now=now)
        bars = get_prices(
            data, commodity=c, start=as_of.date() - timedelta(days=_DAILY_LOOKBACK_DAYS), end=as_of.date(),
            as_of=as_of, client=market_client, now=now,
        )
        risk[c] = risk_facts(bars, specs[c])

    results = {}
    for name in ("manager", "shadow"):
        prev = _last_cycle(storage, name, cycle_id)
        book = PaperBook(
            prev["closing_state"] if prev else None,
            starting_equity=config.starting_equity, stop_out_level=config.stop_out_level,
        )
        opening_state = book.state()
        stop_outs = []
        if prev:
            since = datetime.fromisoformat(prev["as_of"])
            # Captured now, but only bars that opened before the scheduled time (`end`). Every
            # symbol, not just the ones held: a replayed book may hold what this one didn't.
            h1 = {
                c: get_h1_bars(
                    data, commodity=c, start=since, end=as_of, as_of=now, client=market_client, now=now,
                    fetch=not replaying,
                )
                for c in COMMODITIES
            }
            held = {p["symbol_name"] for p in opening_state["positions"]}
            stop_outs = book.replay({c: h1[c] for c in held}, specs, since, as_of)
        snapshot = book.snapshot(quotes, specs)
        breaker_active, reset_applied = _breaker(prev, snapshot, book, breaker_reset_at(), config)
        lots = _lots(book.state(), specs)
        book_state = {"equity": snapshot["equity"], "lots": lots}
        manager_error = None
        if name == "manager":
            try:
                manager_out = manager(
                    LoggingChatClient(chat_client, log, "manager", cycle_id=cycle_id), as_of=as_of, model=model,
                    calls=calls, lots=lots, risk=risk, equity=snapshot["equity"], free_margin=snapshot["free_margin"],
                )
                proposal = manager_out["proposal"]
            except Exception as exc:
                if not hold_on_manager_error:
                    raise
                # A provider outage must not take the shadow book down with it: None is a hold-all (guardrail 1).
                manager_error = f"{type(exc).__name__}: {exc}"
                proposal = None
        else:
            proposal = shadow_proposal(calls, book_state, risk, config.shadow_risk_budget)
        applied, violations = apply_guardrails(proposal, book_state, calls, specs, risk, config.guardrails, breaker_active)
        fills = book.apply_fills(applied, quotes, specs, timestamp)
        book.mark_to(quotes)
        closing = book.snapshot(quotes, specs)
        record = {
            "cycle_id": cycle_id, "book": name, "as_of": timestamp, "quoted_at": quoted_at.isoformat(),
            "opening_state": opening_state,
            "calls": calls, "failed_commodities": [c for c in COMMODITIES if calls[c] is None],
            "stop_outs": stop_outs, "opening_equity": snapshot["equity"],
            "breaker_active": breaker_active, "breaker_reset_applied": reset_applied, "raw_proposal": proposal,
            "manager_error": manager_error,
            "applied_deltas": applied, "violations": violations, "fills": fills,
            "closing_state": book.state(), "closing_lots": _lots(book.state(), specs), **closing,
        }
        for event_type, payload in (
            ("proposal_raw", proposal), ("proposal_applied", applied), ("violations", violations), ("fills", fills),
        ):
            log.append(agent=name, event_type=event_type, payload=payload)
        if manager_error:
            log.append(agent=name, event_type="error", payload={"error": manager_error})
        if not off_schedule:
            storage.put_if_absent(f"portfolio/{name}/cycles/{cycle_id}.json", json.dumps(record).encode())
        results[name] = record
    return {"cycle_id": cycle_id, **results}

import inspect
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ags.llm.client import trace
from ags.llm.loop import ChatClient
from ags.logging.run_log import RunLog
from ags.pipeline import LoggingChatClient
from ags.portfolio.demo_book import DemoBook
from ags.portfolio.guardrails import GuardrailConfig, apply_guardrails
from ags.portfolio.manager import run_portfolio_manager
from ags.portfolio.paper_book import PaperBook
from ags.portfolio.risk import risk_facts, unsized_risk_facts
from ags.portfolio.shadow import shadow_proposal
from ags.portfolio.stores import DEMO_MANAGER, PAPER_MANAGER, SHADOW
from ags.storage import LocalStorage, Prefixed, Storage
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, get_h1_bars, get_prices, get_quote, get_symbol_specs

COMMODITIES = list(CTRADER_SYMBOL_NAMES)
_DAILY_LOOKBACK_DAYS = 45
# The manager call is tried this many times before the cycle holds every manager leg.
MANAGER_ATTEMPTS = 2


@dataclass(frozen=True)
class PortfolioConfig:
    guardrails: GuardrailConfig
    starting_equity: float
    stop_out_level: float
    shadow_risk_budget: float
    drawdown_breaker: float
    manager_book: str = "paper"  # "demo": the manager trades the Pepperstone demo account

    @classmethod
    def from_mapping(cls, env) -> "PortfolioConfig":
        """Limits come from config (env vars set by Terraform), defaults per ARCHITECTURE.md."""

        def num(key: str, default: float) -> float:
            return float(env.get(key) or default)

        return cls(
            guardrails=GuardrailConfig(
                per_commodity_cap=num("PER_COMMODITY_CAP", 1.0), grain_bucket_cap=num("GRAIN_BUCKET_CAP", 1.5),
                gross_leverage_cap=num("GROSS_LEVERAGE_CAP", 3.0), margin_buffer=num("MARGIN_BUFFER", 0.40),
                min_resize_fraction=num("MIN_RESIZE_FRACTION", 0.20), drift_limit_atr=num("DRIFT_LIMIT_ATR", 0.5),
            ),
            starting_equity=num("STARTING_EQUITY_USD", 10_000), stop_out_level=num("STOP_OUT_MARGIN_LEVEL", 0.50),
            shadow_risk_budget=num("SHADOW_RISK_BUDGET", 0.01), drawdown_breaker=num("DRAWDOWN_BREAKER", 0.60),
            manager_book=(env.get("MANAGER_BOOK") or "paper").lower(),
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


def _demo_book(trading_gateway, specs, cycle_id, as_of, prev, config) -> tuple[DemoBook, dict]:
    """The manager's book on the (shared) demo account, with its equity carried from its own records.

    `prev` is the last record in the demo store, so the first demo cycle has none and starts from scratch:
    the balance is `starting_equity` (the capital allocated to this strategy), and from then on it is the
    previous record's balance. Equity is that capital +/- the manager's own P&L, whatever else trades on the
    account, and margin is measured against it, not against the account.
    """
    if trading_gateway is None:
        raise ValueError("MANAGER_BOOK=demo needs a trading gateway")
    state = (prev or {}).get("closing_state") or {}
    book = DemoBook(
        trading_gateway(), specs, cycle_id=cycle_id,
        balance=state["balance"] if prev else config.starting_equity, peak_equity=state.get("peak_equity"),
        marks=state.get("marks"), known_position_ids=[i for p in state.get("positions", []) for i in p["position_ids"]],
    )
    if not prev and book.state()["positions"]:
        raise RuntimeError(f"{cycle_id}: the first demo cycle must start flat, but the account has open `ags` positions")
    if book.already_traded(as_of):
        raise RuntimeError(f"{cycle_id}: the manager already sent orders since {as_of.isoformat()}; not trading twice")
    closes = book.reconcile(datetime.fromisoformat(prev["as_of"])) if prev else []
    return book, {"reconciled_closes": closes}


def _mid(quote: dict) -> float:
    return (quote["bid"] + quote["ask"]) / 2


def reference_prices(calls: dict) -> dict[str, float | None]:
    """Each call's intraday reference mid (the price its analysts saw); None for a daily-close or failed call."""
    out: dict[str, float | None] = {}
    for c in COMMODITIES:
        price = (calls.get(c) or {}).get("price_at_call") or {}
        out[c] = price["price"] if price.get("kind") == "intraday_quote" else None
    return out


def drift_facts(reference: dict, quotes: dict, risk: dict) -> dict:
    """Move from the reference quote to the execution quote, per symbol, in percent and in ATRs."""
    out = {}
    for c in COMMODITIES:
        if reference.get(c) is None:
            out[c] = None
            continue
        move = _mid(quotes[c]) - reference[c]
        atr = risk[c].get("atr")  # None when the symbol's ATR couldn't be measured
        out[c] = {"pct": move / reference[c], "atr": move / atr if atr else None}
    return out


def fresh_quotes(
    data, market_client, decision_quotes: dict, after: datetime, *, replaying: bool = False,
    execution_quoted_at: datetime | None = None,
) -> tuple[dict, str | None]:
    """Execution quotes: captured now, after the manager's model call. Falls back to the decision quotes.

    A replay never fetches: it reads back the quotes the official cycle captured at `execution_quoted_at`
    (its own `exec_quoted_at`), or keeps the decision quotes when the official cycle had none.
    """
    if replaying and execution_quoted_at is None:
        return decision_quotes, None
    at = execution_quoted_at or max(datetime.now(timezone.utc), after + timedelta(seconds=1))
    try:
        quotes = {
            c: get_quote(data, commodity=c, as_of=at, client=market_client, now=after if replaying else at)
            or decision_quotes[c]
            for c in COMMODITIES
        }
    except Exception:
        return decision_quotes, None
    return quotes, at.isoformat()


def _accepts(fn, name: str) -> bool:
    """Whether `fn` takes keyword `name` (a replay's manager variant may predate it)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def fill_costs(fills: list[dict], quotes: dict, reference: dict | None = None) -> dict:
    """What trading cost this cycle, in account currency (positive = cost).

    `spread` is each fill's price against the quote midpoint at decision time (spread plus slippage).
    `drift` is the execution midpoint against the call's intraday reference quote (the price analysts
    saw), on what was traded. `commission` and `swap` are the broker's own, so zero for the paper book.
    """
    spread = drift = 0.0
    for f in fills:
        if f.get("status", "filled") != "filled":
            continue
        q = quotes[f["symbol"]]
        sign = 1 if f["trade_side"] == "BUY" else -1
        units = f["volume"] / 100 * sign
        spread += (f["price"] - _mid(q)) * units
        ref = (reference or {}).get(f["symbol"])
        if ref is not None:
            drift += (_mid(q) - ref) * units
    return {
        "spread": spread, "drift": drift, "commission": -sum(f.get("commission", 0.0) for f in fills),
        "swap": -sum(f.get("swap", 0.0) for f in fills),
        "failed_orders": sum(f.get("status", "filled") != "filled" for f in fills),
    }


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
    data: Storage | None = None,
    quoted_at: datetime | None = None,
    execution_quoted_at: datetime | None = None,
    manager=run_portfolio_manager,
    hold_on_manager_error: bool = True,
    hold_books=lambda: False,
    replace_log: bool = False,
    trading_gateway=None,
) -> dict:
    """One portfolio cycle. Live by default; a counterfactual replay passes `data` (the official
    market-data store, read-only) and `quoted_at` (the official cycle's capture instant), so the
    cycle reads back exactly the market data the official one captured and never fetches any,
    and may swap in another `manager` (same signature as `run_portfolio_manager`). With
    `hold_on_manager_error=False` a failed manager call raises instead of holding every leg, so
    a replay is retried rather than recording an outage as the manager's decision.

    `hold_books()`, asked once both books are computed, holds the whole slot out of the books
    (logged, not written) so it can be retried: the handler's answer when a provider blocked the
    cycle. The result's `held` says whether it did. `replace_log`: a retry, whose earlier attempt
    the caller has archived, starts the cycle's log over.
    """
    # Same upload-once-in-a-finally treatment as a commodity run's log on non-local storage.
    log = RunLog(
        storage, f"portfolio/logs/{cycle_id}", buffered=not isinstance(storage, LocalStorage), fresh=replace_log
    )
    try:
        with trace(chat_client, cycle_id):
            return _run_portfolio(
                cycle_id, calls, storage, log, as_of=as_of, now=now, chat_client=chat_client, model=model,
                market_client=market_client, config=config, breaker_reset_at=breaker_reset_at,
                data=data, quoted_at=quoted_at, execution_quoted_at=execution_quoted_at, manager=manager, hold_books=hold_books,
                hold_on_manager_error=hold_on_manager_error, trading_gateway=trading_gateway,
            )
    finally:
        log.close()


def _run_portfolio(
    cycle_id, calls, storage, log, *, as_of, now, chat_client, model, market_client, config, breaker_reset_at,
    data, quoted_at, execution_quoted_at, manager, hold_on_manager_error, hold_books, trading_gateway,
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
        try:
            bars = get_prices(
                data, commodity=c, start=as_of.date() - timedelta(days=_DAILY_LOOKBACK_DAYS), end=as_of.date(),
                as_of=as_of, client=market_client, now=now,
            )
            risk[c] = risk_facts(bars, specs[c])
        except Exception as exc:
            # One symbol's daily bars (too few, or a failed fetch) must not take the cycle down:
            # its leg is held (guardrail 9). Without a quote there is nothing to price it at either.
            if quotes[c] is None:
                raise
            risk[c] = unsized_risk_facts(quotes[c], specs[c], f"{type(exc).__name__}: {exc}")
    risk_errors = {c: r["error"] for c, r in risk.items() if "error" in r}

    reference = reference_prices(calls)
    exec_quotes, exec_quoted_at = None, None
    held = False
    stores: dict[str, str] = {}
    results = {}
    for name in ("manager", "shadow"):
        # A counterfactual replay never trades: it reads recorded market data and has no broker.
        demo = name == "manager" and config.manager_book == "demo" and not replaying
        store = SHADOW if name == "shadow" else (DEMO_MANAGER if demo else PAPER_MANAGER)
        stores[name] = store
        prev = _last_cycle(storage, store, cycle_id)
        demo_extra: dict = {}
        if demo:
            book, demo_extra = _demo_book(trading_gateway, specs, cycle_id, as_of, prev, config)
        else:
            book = PaperBook(
                prev["closing_state"] if prev else None,
                starting_equity=config.starting_equity, stop_out_level=config.stop_out_level,
            )
        opening_state = book.state()
        stop_outs = []
        if prev and not demo:  # the broker applies the demo account's swap and stop-outs itself
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
        decision_drift = drift_facts(reference, quotes, risk)
        manager_error = None
        if name == "manager":
            proposal = None
            extra = {"drift": decision_drift} if _accepts(manager, "drift") else {}
            for attempt in range(1, MANAGER_ATTEMPTS + 1):
                try:
                    manager_out = manager(
                        LoggingChatClient(chat_client, log, "manager", cycle_id=cycle_id), as_of=as_of, model=model,
                        calls=calls, lots=lots, risk=risk, equity=snapshot["equity"], free_margin=snapshot["free_margin"],
                        **extra,
                    )
                except Exception as exc:
                    manager_error = f"{type(exc).__name__}: {exc}"
                    log.append(agent=name, event_type="error", payload={"error": manager_error, "attempt": attempt})
                    if attempt == MANAGER_ATTEMPTS and not hold_on_manager_error:
                        raise
                else:
                    manager_error, proposal = None, manager_out["proposal"]
                    break
            # A provider outage must not take the shadow book down with it: None is a hold-all
            # (guardrail 1). The handler alerts on `manager_error`.
            # The model call takes a while: both books trade on quotes captured after it, the same ones.
            exec_quotes, exec_quoted_at = fresh_quotes(
                data, market_client, quotes, now, replaying=replaying, execution_quoted_at=execution_quoted_at,
            )
            # Decided before any order goes out: a held slot is retried later, so it must not have traded.
            held = hold_books()
        else:
            proposal = shadow_proposal(calls, book_state, risk, config.shadow_risk_budget)
        drift = drift_facts(reference, exec_quotes, risk)
        applied, violations = apply_guardrails(
            proposal, book_state, calls, specs, risk, config.guardrails, breaker_active,
            drift={c: d["atr"] if d else None for c, d in drift.items()},
        )
        # A held slot is retried later, so the demo account must not have traded it (the paper books are not written).
        fills = [] if (held and demo) else book.apply_fills(applied, exec_quotes, specs, timestamp)
        book.mark_to(exec_quotes)
        closing = book.snapshot(exec_quotes, specs)
        record = {
            "cycle_id": cycle_id, "book": name, "book_kind": "demo" if demo else "paper", "as_of": timestamp,
            "quoted_at": quoted_at.isoformat(), "opening_state": opening_state, **demo_extra,
            "calls": calls, "failed_commodities": [c for c in COMMODITIES if calls[c] is None],
            "stop_outs": stop_outs, "opening_equity": snapshot["equity"],
            "breaker_active": breaker_active, "breaker_reset_applied": reset_applied, "raw_proposal": proposal,
            "manager_error": manager_error, "risk_errors": risk_errors,
            "applied_deltas": applied, "violations": violations, "fills": fills,
            "reference_prices": reference, "exec_quoted_at": exec_quoted_at, "drift": drift,
            "costs": fill_costs(fills, exec_quotes, reference), "closing_state": book.state(), "closing_lots": _lots(book.state(), specs), **closing,
        }
        for event_type, payload in (
            ("proposal_raw", proposal), ("proposal_applied", applied), ("violations", violations), ("fills", fills),
        ):
            log.append(agent=name, event_type=event_type, payload=payload)
        results[name] = record
    # Both books or neither: a held slot is written by its retry, or not at all.
    held = hold_books()
    if held:
        log.append(agent="portfolio", event_type="held", payload={"reason": "blocking provider error"})
    else:
        for name, record in results.items():
            storage.put_if_absent(f"portfolio/{stores[name]}/cycles/{cycle_id}.json", json.dumps(record).encode())
    return {"cycle_id": cycle_id, "held": held, **results}

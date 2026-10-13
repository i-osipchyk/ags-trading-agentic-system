# Ags Trading Analyst — Pipeline Design v3

## Overview

A twice-daily, scheduled research pipeline that produces a bullish/bearish/neutral call with a written thesis for a fixed basket of agricultural futures (traded as Pepperstone CFDs), and a **paper portfolio** that an LLM manager scales up or down based on those calls. It is a research and evaluation tool, not an execution system: nothing sends an order to a broker. Position changes are applied only to a simulated, cTrader-shaped paper book.

This version is scoped as a **lean, forward-only, 2-month experiment**. There is no statistical success threshold. The auditor grades every call as it comes due, and the paper book marks to market every cycle; at the 2-month mark, all graded results and the paper P&L collected so far are reviewed qualitatively (judgment call, not a formula) to decide whether this type of AI-assisted synthesis is usable for trading decisions at all. No second checkpoint is scheduled — if the answer is yes, next steps (DB migration, model tiering, multi-horizon calls, backtesting, live execution) get decided then, not pre-committed now.

**v3 changes from v2** (grilling session, 2026-10-12): weekly manual runs → scheduled runs at 16:00 and 04:00 ET on weekdays, on AWS; a new cross-commodity portfolio stage (manager agent + deterministic guardrails + paper book + shadow book); a 1-day diagnostic audit checkpoint; Telegram alerts and a daily digest; the 2-month review clock restarts at the first scheduled AWS cycle.

Agents: four independent analysts (Technical, News, Weather/Season, Supply/Demand) that run in parallel and blind to each other, a Coordinator per commodity that synthesizes their outputs into one call, a Portfolio Manager that turns the seven calls into lot changes on the paper book, and an Auditor that grades each call against price and against each named driver at fixed lags (1 day diagnostic; 1, 2, 4, 8 weeks).

## Architecture

The four analysts never see each other's output or the coordinator's running view — this is what keeps their signals independent and auditable per-agent. Neither the analysts nor the coordinators ever see the paper book: a coordinator that knows the current position and its P&L starts defending it instead of calling the market. The auditor runs on a separate schedule, grading each call once enough time has passed to know the outcome.

One call per commodity per cycle, at a single fixed 1-week horizon. No multi-horizon fanout, and no adaptive follow-up call from the coordinator back to an individual analyst — both were considered and dropped for this version to keep the run shape fixed and the analysts genuinely independent (see Guardrails).

The portfolio stage runs once per cycle, after all seven commodity pipelines finish. It is the only stage that sees across commodities, and the only one that sees the book.

## Orchestration flow

Scheduled: **16:00 and 04:00 ET, Monday–Friday**, no weekend runs. 16:00 is after every market in the basket has closed (clean daily marks; on Fridays, 30 minutes after Commitments of Traders). 04:00 picks up overnight news and the Asia/Europe sessions before the US open; its marks come from thin overnight trading and are noisier — accepted for a paper book.

1. The scheduler starts a cycle, tagged with a `cycle_id` and trigger timestamp.
2. For each of the seven commodities, in parallel, a commodity pipeline runs under its own `run_id` (sharing the cycle's trigger timestamp):
   1. Technical, News, Weather/Season, and Supply/Demand analysts run in parallel, each scoped to its own tools, blind to the other three and to the coordinator's running view.
   2. Coordinator collects all four structured outputs and synthesizes a single call: bullish, bearish, or neutral, with conviction, thesis, and driver attribution — one call, 1-week horizon.
3. Portfolio stage, once, after all seven pipelines finish (a failed commodity doesn't block it — that leg is held):
   1. Code builds the manager's inputs: the seven calls, current lots per symbol, ATR, $ value of 1 ATR per lot, equity, free margin.
   2. Manager proposes a signed lot delta per symbol.
   3. Deterministic guardrails clamp or reject the proposal.
   4. Code fills the surviving deltas on the paper book (Pepperstone-faithful costs), and runs the shadow book's mechanical sizer through the same guardrails and fills.
4. Everything (inputs, intermediate agent outputs, calls, raw and post-guardrail proposals, violations, fills) is logged under `run_id` / `cycle_id`, with every fetched data point tagged with its `as_of` capture timestamp.
5. Auditor runs separately, on a lag, grading each `run_id` at 1 day (diagnostic) and 1, 2, 4, and 8 weeks after it fired (see Auditor).
6. A daily digest goes out at 17:00 ET on weekdays; failures alert immediately (see Notifications).

## Technical analyst

Trend, volatility, and seasonality context. Trend and volatility are computed deterministically by code; the agent's own judgment is reserved for the one place it adds value — whether the current move is typical or atypical for this point in the calendar.

| Tool | Purpose |
| --- | --- |
| `get_prices(symbol, start, end)` | Roll-adjusted OHLC |
| `get_trend_state(symbol)` | Trend signal and its age (code-computed, not re-derived by the agent) |
| `get_seasonality(symbol)` | Historical seasonal pattern for this calendar week |

**Correction to this version's tool list:** a `get_curve(symbol)` tool (term
structure / calendar spreads) was originally planned here. Dropped — cTrader,
a retail CFD broker, only exposes one continuous rolling instrument per
commodity (plus cosmetic margin/swap-free variants), not multiple contract
months, so there's no curve to compute from it. A real term structure would
need an exchange or paid vendor feed (CME/ICE settlement data); revisit after
the 2-month review if this turns out to matter, same bucket as the other
deferred items in `ARCHITECTURE.md`.

**Chart description.** The analyst is also handed the latest N daily candles
(raw OHLCV) plus their EMA values (same spans as `get_trend_state`) as a
precomputed fact, not a tool — there's no parameter for the agent to choose,
it's always "the latest N as of `as_of`," so a tool call would add nothing.
N is `CHART_LOOKBACK_CANDLES` (env var, default 30). The agent's job is to
turn that into a short narrative (`chart_description`), the same
judgment-not-recomputation split as `seasonality_alignment`.

**Output schema**

| Field | Contents |
| --- | --- |
| `trend` | up / down / sideways, since when |
| `volatility` | current vs. trailing average |
| `seasonality_alignment` | typical or atypical for this calendar week |
| `key_levels` | recent range, breakout points |
| `chart_description` | narrative read of the latest N candles + EMAs |
| `degraded` | bool — true if an underlying source hasn't updated since this analyst's last run, or a tool call failed/timed out |

## News analyst

Reports what's being said, not why it matters — no macro or technical commentary. The underlying commodity is the same whether traded as futures or CFDs, so this agent's sourcing doesn't need to change for CFD execution.

| Tool | Purpose |
| --- | --- |
| `search_news(query, as_of)` | Web search, filtered to items published before `as_of` |
| `fetch_document(url)` | Full text of a report or article |

**Output schema**

A deduped list of `{headline, source, date, direction, one-line reason}`, plus a top-level `degraded: bool` (true if a search/fetch call failed or returned nothing new since the last run).

## Weather / season analyst

Crop growth stage matters as much as the raw weather signal — a dry spell before planting is noise, the same dry spell during flowering is a real threat.

| Tool | Purpose |
| --- | --- |
| `get_weather(region, as_of)` | Drought monitor, precip/temp anomalies, forecasts as issued (not reanalysis) |
| `get_growing_calendar(symbol)` | What growth stage the crop is at right now |

**Output schema**

| Field | Contents |
| --- | --- |
| `regions_covered` | Regions assessed this run |
| `current_conditions` | Observed conditions per region |
| `stage_of_crop_cycle` | e.g. flowering, harvest, dormant |
| `risk_assessment` | benign / watch / severe, per region |
| `degraded` | bool — true if a source hasn't updated since this analyst's last run, or a tool call failed/timed out |

## Supply / demand analyst

Reads structured releases only — USDA, UNICA, Conab, ISMA, ISO — so its input never overlaps with the news analyst's free-text sourcing.

| Tool | Purpose |
| --- | --- |
| `get_usda_report(report, as_of)` | Latest WASDE, Crop Progress, Export Sales, etc., as released, plus change from prior release |
| `get_balance_sheet_revisions(symbol)` | Month-over-month change in ending stocks and stocks-to-use, from the point-in-time store |
| `get_industry_data(source)` | Commodity-specific feeds (UNICA/Conab for sugar, ICO for coffee, etc.) |

**Output schema**

| Field | Contents |
| --- | --- |
| `latest_report` | Report name and date |
| `key_figures` | `{figure, value, prior, revision_direction}` list |
| `stocks_to_use` | Current value and trend (tightening / loosening) |
| `degraded` | bool — true if the expected report hasn't been released/updated since this analyst's last run, or a tool call failed/timed out |

## Coordinator

Synthesizes, doesn't re-research. No raw data tools beyond `read_thesis_log(symbol)` (its own last few weeks' calls, for continuity). It must attribute the call to which analysts drove it, and surface disagreement explicitly rather than smoothing it over — that's the field that later shows whether it's doing real synthesis or just picking the loudest voice.

No follow-up call back to an individual analyst — dropped for this version. It conflicts with fixed run shape and the analysts' independence, and any benefit is speculative until the fixed baseline has been tried.

**Output schema**

| Field | Contents |
| --- | --- |
| `call` | bullish / bearish / neutral |
| `conviction` | 1–5, rubric-anchored (see below) |
| `horizon` | fixed: 1 week |
| `price_at_call` | Price and timestamp |
| `supporting_analysts` | Which of the four agreed |
| `dissenting_analysts` | Which disagreed |
| `thesis` | Short synthesis text |
| `key_drivers` | Pulled from analysts, source attribution kept intact |
| `invalidation_conditions` | What would prove the call wrong |

**Conviction rubric** — anchored to analyst agreement/dissent, not a free judgment call:

| Score | Meaning |
| --- | --- |
| 5 | All four analysts agree, no material dissent |
| 4 | Three of four agree, dissent is minor/tangential |
| 3 | Split or mixed signals, coordinator is going with a plurality read |
| 2 | One or two weak signals against a backdrop of mostly neutral/no-signal analysts |
| 1 | Coordinator is calling a direction mostly on priors/continuity (`read_thesis_log`), not fresh evidence this week |

## Auditor

Runs at each checkpoint (1, 2, 4, 8 weeks after a `run_id`), blind to any newer run so it never blends timelines, regardless of the coordinator's fixed 1-week horizon. Only the 1-week checkpoint is the pass/fail verdict on the call; the 2/4/8-week checkpoints are diagnostic context (e.g. "directionally right at 1 week, but fully reversed by week 4" is exactly the kind of signal the 2-month review needs).

Grades both the coordinator's final call and each analyst's individual output, so the scorecard can separate "the pipeline was wrong" from "one analyst was unreliable."

**Inputs per checkpoint**: the frozen original thesis and drivers, the actual price path since the call, and what happened to each named driver (was the EU crop estimate revised again, did positioning unwind, etc.).

**Output schema**

| Field | Contents |
| --- | --- |
| `price_outcome` | Return over the horizon, vs. a trend-only benchmark over the same period |
| `direction_correct` | bool — **not scored for neutral calls** (excluded entirely; a neutral call is graded only via `thesis_vs_outcome`) |
| `driver_verdicts` | Per driver: `{driver, played_out: yes/no/partially, evidence}` |
| `calibration_note` | Did conviction 5 resolve cleanly, or was it followed by chop |
| `thesis_vs_outcome` | Did price move for the stated reason, or for something else — right call, wrong reason is a distinct, important case. For neutral calls, this is the only grade. |

Disagreement between the auditor and the original analyst is logged as-is, not resolved in the auditor's favor — it's diagnostic of a prompt or rubric problem either way.

**1-day diagnostic checkpoint.** In addition to 1/2/4/8 weeks, every call is graded at 1 day, same output schema. Diagnostic only — the 1-week checkpoint remains the pass/fail verdict.

**Overlapping calls.** With ten cycles a week and a 1-week horizon, consecutive calls for the same commodity share most of their price path; they are not independent samples. Any aggregate scorecard (hit rate, calibration, per-analyst accuracy) reports both the raw count and an **effective count**, weighting each call by `1 / k` where `k` is the number of same-commodity calls whose 1-week windows overlap it (≈10 in steady state) — roughly one independent observation per commodity per week. A scorecard that shows only raw counts overstates confidence by about 10×.

## Portfolio manager

Runs once per cycle, after the seven coordinators. Decides **how much and where**; code does every unit conversion, cap, fill and cost after that.

**Inputs** (nothing else — not the raw analyst outputs, not any track record):

| Input | Source |
| --- | --- |
| Seven coordinator calls | `{symbol, call, conviction, thesis, key_drivers}`; `null` for a commodity whose pipeline failed |
| Current position per symbol | Signed lots, from the paper book |
| `atr` per symbol | Code-computed (daily bars, completed bars only) |
| `atr_usd_per_lot` per symbol | Code-computed: ATR × contract size × $/point, so cross-symbol risk is comparable without the LLM doing contract arithmetic |
| `equity`, `free_margin` | From the paper book |

Not given: the raw analyst outputs (it would start redoing the coordinators' synthesis, and the auditor couldn't tell whose judgment drove an allocation), its own track record, or the coordinators' track records (too short and noisy at this cadence; stacking a second feedback loop on the coordinator's own makes both unreadable). Revisit after a few weeks of graded allocations.

**Output schema**

| Field | Contents |
| --- | --- |
| `legs` | One per symbol, all seven present: `{symbol, delta_lots, rationale}` — `delta_lots` is a signed number added to the current position |
| `portfolio_note` | Short cross-commodity reasoning (e.g. how it treated the correlated grains) |

A neutral call freezes its leg: code forces `delta_lots = 0` regardless of what the manager returned.

## Portfolio guardrails

Deterministic checks between the manager's proposal and the paper book. All numeric limits live in config, not code. Every cycle logs three views of the proposal: **raw**, **post-guardrail**, and the **violation list** — so the manager is gradable on what it proposed, not only on what got applied.

| # | Check | Rule | On violation |
| --- | --- | --- | --- |
| 1 | Schema | Valid structured output, all 7 symbols present | Reject whole proposal → hold everything |
| 2 | Direction consistency | Bullish → leg may end long or flat; bearish → short or flat; neutral → held exactly (delta forced to 0). A position against the call must be closed. | A delta that would end the leg on the wrong side of zero is **clamped to flat** for that leg only (not a whole-proposal reject) |
| 3 | Per-commodity cap | Notional ≤ 1.0× equity | Clamp that leg |
| 4 | Grain bucket | Corn + soybeans + wheat, net same-direction notional ≤ 1.5× equity | Scale the grain legs proportionally |
| 5 | Gross leverage | Sum of absolute notionals ≤ 3.0× equity | Scale all legs proportionally |
| 6 | Margin buffer | Used margin ≤ 40% of equity | Scale down |
| 7 | No-churn threshold | Resize < 20% of the current leg, or < 1 volume step | Drop that trade |
| 8 | Drawdown breaker | Equity < 80% of peak → reductions only, until manually reset | Clamp to ≤ current size |
| 9 | Missing input | A commodity whose pipeline failed | Hold that leg as-is, override the manager's line |

Guardrails 3–6 and a stop-out can still shrink a neutral-frozen leg; nothing else moves it. A symbol whose minimum volume already exceeds its per-commodity cap simply can't be held — reported as a violation, never silently rounded up.

## Paper book

A local-simulated portfolio with the same shape as a Pepperstone cTrader account, so moving to the real account later swaps the backend, not the data model.

- **Starting equity:** $10,000 USD, on margin.
- **Fills:** at the bid (sells) or ask (buys) from the cTrader quote at fill time, captured point-in-time so fills replay.
- **Volumes:** rounded to the symbol's min / step / max volume from cTrader symbol specs.
- **Swap:** charged at each daily rollover from the symbol's long/short swap rates (including the triple-swap day).
- **Margin:** used margin computed from the symbol's margin rate.
- **Stop-out replay:** the broker would stop out continuously; the book is only touched every 12 hours. Each cycle first replays intraday bars since the last cycle and applies any stop-out that would have triggered, at the stop-out price. Otherwise the paper book survives drawdowns that would have closed a real account.

Neutral calls carry positions: a long opened on a bullish call is held through any run of neutral calls, and exits only via a manager reduction on a later bullish call, a forced close on a bearish call, a guardrail, or a stop-out.

## Shadow book

A second paper book, same starting equity, same guardrails, same costs, sized by a fixed mechanical rule instead of the manager:

`target_lots = sign(call) × (conviction / 5) × (risk_budget × equity) / atr_usd_per_lot`, neutral legs held, `delta = target − current`.

The manager's value is **manager P&L − shadow P&L**. Both books trade the same calls, so the difference isolates the sizing judgment. No LLM calls; computed in the same portfolio stage.

## Notifications

Telegram, one chat:

- **Alerts (immediate):** cycle execution failed; a commodity failed two cycles in a row; drawdown breaker tripped; cTrader token refresh failed (platform alarm); Lambda error alarms.
- **Daily digest (17:00 ET, weekdays):**
  1. **Spend** — LLM cost for the last 24 h and month-to-date, by agent and by commodity; AWS cost from Cost Explorer (labeled as the prior day, since it lags).
  2. **Book** — equity, day change, drawdown from peak, open positions in lots, margin used.
  3. **Manager vs shadow** — cumulative P&L of each and the difference.
  4. **Latest calls** — seven rows: direction, conviction, change since the prior cycle.

No spending cap; the digest's spend section is the control.

## Review checkpoint (replaces open questions)

At **2 months** after the first scheduled AWS cycle (the clock restarted with v3 — the earlier weekly manual runs used a different run shape and are archived, not pooled):

- Pull every graded call/run collected so far, using whatever checkpoint data exists at that point. Calls made near the end of the window will only have short-horizon grades (1-day, 1/2-week) — that's expected, not a gap to wait out. No second review is scheduled to let long-horizon data catch up.
- Assess qualitatively, per analyst and for the coordinator as a whole: is this producing anything useful? Use effective counts, not raw counts (see Auditor).
- Assess the portfolio: paper equity curve, drawdown, costs paid, and manager-vs-shadow P&L.
- No fixed statistical bar. The question is judgment-based and effectively binary: is this type of AI-assisted analysis worth continuing to use for trading decisions.
- If yes, decide next steps then (DB migration, model tier split, automated trigger, multi-horizon calls, backtesting) rather than pre-committing now.

## Chat interface

A read-only chat interface into the coordinator, scoped to a single frozen `run_id`. Lets you ask things like "why did you call corn bearish this week" and get an answer built only from that run's already-logged analyst outputs, the coordinator's own thesis, and `read_thesis_log` — no live tool calls, no fresh data pulled at chat time.

This interface is free-form and intentionally unscored — a debugging/insight layer for understanding a past decision, not a second, unscheduled coordinator decision point. Its output is not written into the audited pipeline record.

## Data layer and point-in-time enforcement

Every tool takes an `as_of` timestamp, enforced by the data layer itself, not by agent prompting. This is what makes the pipeline usable for both backtesting and live forward runs without future information leaking in, and it's the single most important guardrail in the whole design.

**Storage:** flat, dated objects for now — one per `{source, report, release timestamp}`, immutable once written, named so a listing alone tells you what was known as of any moment. They live in S3 (the official record), mirrored read-only to the local machine. No database yet; that's a decision to revisit only if the 2-month review says this is worth continuing.

**Forward-only:** this experiment runs live/forward only, no historical backtesting. Every fetched record is still tagged with its `as_of` capture date regardless, so a future backtest remains possible without redesigning the store. The backtest-leakage guardrail below doesn't apply yet for this reason, but stays documented for whenever backtesting is added.

Additional tools worth adding early:

- `get_positioning(symbol)` — COT managed-money net position and its percentile, keyed to release date, not the as-of date.
- `get_related(symbol)` — cross-market context (energy for sugar/corn, USD and BRL for sugar/coffee, freight, fertilizer).
- `get_base_rate(symbol, conditions)` — how the trend signal has historically performed under similar conditions, so agents calibrate against a number instead of relying purely on narrative.

No tool in this pipeline places orders with a broker. The portfolio manager proposes lot changes; deterministic code applies guardrails and fills them on the paper book only (see Portfolio manager, Portfolio guardrails, Paper book). Live execution is out of scope for this design.

## Design principles and guardrails

- **No cross-contamination.** Analysts run in parallel, blind to each other and to the coordinator's running view. Anchoring is the main risk a sequential design would introduce.
- **No live execution anywhere in the pipeline.** Every agent produces a proposal or an analysis, never an order. Proposals reach only the paper book, through deterministic guardrails. A test bans cTrader trading messages from the codebase.
- **The book is invisible upstream.** Analysts and coordinators never see positions or P&L; only the portfolio manager does.
- **Structured output only** for the scored pipeline. Free-form text can't be scored or compared cycle over cycle; every agent returns fixed fields. (The read-only chat interface is a deliberate, separate exception — see Chat interface.)
- **Everything logged**, keyed by `run_id` and `cycle_id`: prompts, tool calls, tool results, outputs, raw and post-guardrail manager proposals, violations, and fills, so any run can be replayed or audited.
- **Fixed run shape.** Same four analysts, same commodity universe, same single 1-week-horizon call, same single portfolio stage, every scheduled cycle — comparability across cycles matters more than adaptive depth, since the auditor's per-analyst scorecard depends on it. No adaptive fanout, no coordinator-to-analyst follow-up calls.
- **One writer to the official record.** Only scheduled cycles on AWS write official runs; only the portfolio stage writes the paper book. Local runs are unofficial and never sync up.
- **Degraded-input handling.** Every analyst flags `degraded: true` when its primary source hasn't updated since its last run, or a tool call failed/timed out. The analyst still emits a best-effort structured output — a degraded input is surfaced, not used as a reason to skip the run.
- **Bounded cost.** Hard cap of 5 tool calls per agent per run, exposed as an env variable for quick tuning. A run that hits the cap fails loudly and is logged as incomplete, not silently truncated.
- **Backtest leakage control.** Not applicable while forward-only (see Data layer). Revisit if/when historical backtesting is added: strip dates and named episodes where possible, prefer forward paper-testing over backtesting for the cleanest read.

## Model

Same model for every agent for now — start with DeepSeek V4.1 Flash (cheapest tier). Revisit per-agent tiering (e.g. strongest model reserved for the coordinator and auditor) only if cost or quality becomes a problem, or after the 2-month review.

## Commodity universe and cadence

**Universe:** corn, soybeans, wheat, sugar #11, coffee "C", cotton, cocoa — chosen for liquidity and for having free, structured, point-in-time sources (USDA, UNICA, ISO, EU Sugar Market Observatory). Corn, soybeans, and wheat move together, so the basket also needs sugar, coffee, cotton, and cocoa to give independent signals rather than one correlated bet repeated seven times.

**Cadence:** two scheduled cycles per weekday, at 16:00 and 04:00 ET, Monday–Friday, no weekend runs (markets closed, inputs identical). Each cycle runs all seven commodities plus the portfolio stage. Most fundamental inputs (COT, Drought Monitor, export sales weekly; WASDE/Conab/UNICA monthly) are unchanged between consecutive cycles — only prices and news move within 12 hours. That is accepted: the 12-hour beat exists for fast paper-P&L feedback, not because the fundamentals change that fast. If near-duplicate runs turn out to waste spend, splitting the cadence (analysts twice a week, manager every 12 h) is the cheap fallback.

Earlier weekly runs (Friday afternoon ET, manually triggered) are archived as pre-v3 history.

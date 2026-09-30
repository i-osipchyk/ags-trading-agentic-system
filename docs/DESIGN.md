# Ags Trading Analyst — Pipeline Design v2

## Overview

A weekly, on-demand research pipeline that produces a bullish/bearish/neutral call with a written thesis for a fixed basket of agricultural futures. It is a research and evaluation tool, not an execution system: no agent places, sizes, or triggers a trade.

This version is scoped as a **lean, forward-only, 2-month experiment**. There is no statistical success threshold. The auditor grades every call as it comes due; at the 2-month mark, all graded results collected so far are reviewed qualitatively (judgment call, not a formula) to decide whether this type of AI-assisted synthesis is usable for trading decisions at all. No second checkpoint is scheduled — if the answer is yes, next steps (DB migration, model tiering, automation, multi-horizon calls, backtesting) get decided then, not pre-committed now.

Five agents: four independent analysts (Technical, News, Weather/Season, Supply/Demand) that run in parallel and blind to each other, a Coordinator that synthesizes their outputs into one call, and an Auditor that grades each call against price and against each named driver at fixed lags (1, 2, 4, 8 weeks).

## Architecture

The four analysts never see each other's output or the coordinator's running view — this is what keeps their signals independent and auditable per-agent. The auditor runs on a separate schedule, grading each week's call once enough time has passed to know the outcome.

One call per commodity per week, at a single fixed 1-week horizon. No multi-horizon fanout, and no adaptive follow-up call from the coordinator back to an individual analyst — both were considered and dropped for this version to keep the run shape fixed and the analysts genuinely independent (see Guardrails).

## Orchestration flow

Manually triggered per commodity for now — no cron/scheduler yet. Still run at the fixed weekly time (Friday afternoon ET, after Commitments of Traders and most weekly export sales) so snapshots stay comparable week to week even though the trigger itself is manual.

1. You trigger a run for commodity X, tagged with a `run_id` and timestamp.
2. Technical, News, Weather/Season, and Supply/Demand analysts run in parallel, each scoped to its own tools, blind to the other three and to the coordinator's running view.
3. Coordinator collects all four structured outputs.
4. Coordinator synthesizes a single call: bullish, bearish, or neutral, with conviction, thesis, and driver attribution — one call, 1-week horizon.
5. Everything (inputs, intermediate agent outputs, final call) is logged under `run_id`, with every fetched data point tagged with its `as_of` capture date.
6. Auditor runs separately, on a lag, grading a given `run_id` at 1, 2, 4, and 8 weeks after it fired — all four checkpoints run regardless of the 1-week horizon (see Auditor).

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

**Output schema**

| Field | Contents |
| --- | --- |
| `trend` | up / down / sideways, since when |
| `volatility` | current vs. trailing average |
| `seasonality_alignment` | typical or atypical for this calendar week |
| `key_levels` | recent range, breakout points |
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

## Review checkpoint (replaces open questions)

At **2 months** after the first live run:

- Pull every graded call/run collected so far, using whatever checkpoint data exists at that point. Calls made near the end of the window will only have short-horizon grades (1/2-week) — that's expected, not a gap to wait out. No second review is scheduled to let long-horizon data catch up.
- Assess qualitatively, per analyst and for the coordinator as a whole: is this producing anything useful?
- No fixed statistical bar. The question is judgment-based and effectively binary: is this type of AI-assisted analysis worth continuing to use for trading decisions.
- If yes, decide next steps then (DB migration, model tier split, automated trigger, multi-horizon calls, backtesting) rather than pre-committing now.

## Chat interface

A read-only chat interface into the coordinator, scoped to a single frozen `run_id`. Lets you ask things like "why did you call corn bearish this week" and get an answer built only from that run's already-logged analyst outputs, the coordinator's own thesis, and `read_thesis_log` — no live tool calls, no fresh data pulled at chat time.

This interface is free-form and intentionally unscored — a debugging/insight layer for understanding a past decision, not a second, unscheduled coordinator decision point. Its output is not written into the audited pipeline record.

## Data layer and point-in-time enforcement

Every tool takes an `as_of` timestamp, enforced by the data layer itself, not by agent prompting. This is what makes the pipeline usable for both backtesting and live forward runs without future information leaking in, and it's the single most important guardrail in the whole design.

**Storage:** flat, dated files for now — one file per `{source, report, release_date}`, immutable once written, named so a directory listing alone tells you what was known as of any date. No database yet; that's a decision to revisit only if the 2-month review says this is worth continuing.

**Forward-only:** this experiment runs live/forward only, no historical backtesting. Every fetched record is still tagged with its `as_of` capture date regardless, so a future backtest remains possible without redesigning the store. The backtest-leakage guardrail below doesn't apply yet for this reason, but stays documented for whenever backtesting is added.

Additional tools worth adding early:

- `get_positioning(symbol)` — COT managed-money net position and its percentile, keyed to release date, not the as-of date.
- `get_related(symbol)` — cross-market context (energy for sugar/corn, USD and BRL for sugar/coffee, freight, fertilizer).
- `get_base_rate(symbol, conditions)` — how the trend signal has historically performed under similar conditions, so agents calibrate against a number instead of relying purely on narrative.

No tool in this pipeline places orders, sizes positions, or sets risk limits. Deterministic code, not any agent, consumes the coordinator's proposal for sizing and execution — out of scope for this design.

## Design principles and guardrails

- **No cross-contamination.** Analysts run in parallel, blind to each other and to the coordinator's running view. Anchoring is the main risk a sequential design would introduce.
- **No execution authority anywhere in the pipeline.** Every agent produces a proposal or an analysis, never an order.
- **Structured output only** for the scored pipeline. Free-form text can't be scored or compared week over week; every agent returns fixed fields. (The read-only chat interface is a deliberate, separate exception — see Chat interface.)
- **Everything logged**, keyed by `run_id`: prompts, tool calls, tool results, and outputs, so any run can be replayed or audited.
- **Fixed run shape.** Same four analysts, same commodity universe, same single 1-week-horizon call, every week — comparability across weeks matters more than adaptive depth, since the auditor's per-analyst scorecard depends on it. No adaptive fanout, no coordinator-to-analyst follow-up calls.
- **Degraded-input handling.** Every analyst flags `degraded: true` when its primary source hasn't updated since its last run, or a tool call failed/timed out. The analyst still emits a best-effort structured output — a degraded input is surfaced, not used as a reason to skip the run.
- **Bounded cost.** Hard cap of 5 tool calls per agent per run, exposed as an env variable for quick tuning. A run that hits the cap fails loudly and is logged as incomplete, not silently truncated.
- **Backtest leakage control.** Not applicable while forward-only (see Data layer). Revisit if/when historical backtesting is added: strip dates and named episodes where possible, prefer forward paper-testing over backtesting for the cleanest read.

## Model

Same model for every agent for now — start with DeepSeek V4.1 Flash (cheapest tier). Revisit per-agent tiering (e.g. strongest model reserved for the coordinator and auditor) only if cost or quality becomes a problem, or after the 2-month review.

## Commodity universe and cadence

**Universe:** corn, soybeans, wheat, sugar #11, coffee "C", cotton, cocoa — chosen for liquidity and for having free, structured, point-in-time sources (USDA, UNICA, ISO, EU Sugar Market Observatory). Corn, soybeans, and wheat move together, so the basket also needs sugar, coffee, cotton, and cocoa to give independent signals rather than one correlated bet repeated seven times.

**Cadence:** one run per commodity per week, manually triggered, fixed to a single time (Friday afternoon ET), after Commitments of Traders and most weekly export sales have been released, so every week's snapshot is comparable to the last.

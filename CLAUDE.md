# Ags Trading Analyst — Multi-Agent Pipeline

## What this project is

A weekly, on-demand research pipeline that produces a bullish/bearish/neutral
call with a written thesis for a fixed basket of agricultural futures. It is
a **research and evaluation tool, not an execution system** — no agent
places, sizes, or triggers a trade. The point is to find out, with an
auditable track record, whether AI-assisted fundamental and technical
synthesis adds anything beyond a trend-only signal.

Full design writeup (architecture diagram, agent I/O schemas, rollout plan):
https://claude.ai/artifact/3rEgJWBSqQh6ZtERuy37dC

Implementation-level technical architecture (runtime, data layer, external
sources, logging format, and everything else *how*-shaped): `ARCHITECTURE.md`.

## Architecture

Five agents, run once a week per commodity:

- **Technical analyst** — trend, volatility, seasonality alignment
- **News analyst** — recent headlines, deduped, no commentary
- **Weather/season analyst** — regional conditions + crop growth stage
- **Supply/demand analyst** — USDA/UNICA/Conab/ISO report data (plus
  ICO/ICCO/ICAC for coffee/cocoa/cotton as needed — see `ARCHITECTURE.md`)
- **Coordinator** — synthesizes the four into one call. Also reads its own
  track record (`read_track_record`: past calls joined with whatever audit
  checkpoints exist for them) — coordinator-only, not extended to the four
  analysts. See `ARCHITECTURE.md` for why, and for the accepted
  cold-start/comparability caveat this introduces.
- **Auditor** — grades each call at 1/2/4/8 weeks against price and against
  each named driver (runs on a separate, lagged schedule)

The four analysts run **in parallel and blind to each other** — this is
load-bearing, not incidental. Do not give them visibility into each other's
output or the coordinator's running view; that's what keeps their signals
independent and lets the auditor attribute accuracy per agent.

## Hard constraints — do not violate these

- **No execution.** Nothing in this codebase places an order, sizes a
  position, or sets a stop. Every agent's output is a proposal, not an
  action. If a task looks like it's adding execution, stop and confirm with
  the user first.
- **Point-in-time data only.** Every data-access function takes an `as_of`
  timestamp and must not return information that wasn't public as of that
  timestamp. This is the #1 way this kind of backtest silently breaks —
  when adding a new data source, check whether it's PIT-safe (an archived,
  as-released feed) or a "latest revised values" feed (USDA PSD Online,
  NASS Quick Stats — do not use these for backtesting; use ESMIS archives
  instead for USDA reports).
- **Structured output only.** Every agent returns fixed schema fields
  (see the design doc), never free text. Free-form output can't be scored
  or compared week over week.
- **Deterministic where possible.** Trend and volatility are computed by
  code, not re-derived by an LLM call, and passed to the Technical analyst
  as a fact. Keep the LLM's job to synthesis and judgment calls (e.g.
  seasonality alignment), not recomputing things code already knows.
- **Fixed run shape.** Same four analysts, same commodity universe, every
  week. Comparability across weeks is what the auditor's per-agent
  scorecard depends on — don't make the fan-out adaptive without updating
  the design doc first.
- **Everything logged**, keyed by `run_id`: prompts, tool calls, tool
  results, and outputs. Any run must be replayable.

## Commodity universe

Corn, soybeans, wheat, sugar #11, coffee "C", cotton, cocoa — chosen for
liquidity and free point-in-time sources. Corn/soy/wheat move together, so
don't treat the basket as more independent signals than it is.

## Run cadence

Once per commodity per week, fixed to a single time (Friday afternoon ET,
after Commitments of Traders and most weekly export sales) so weekly
snapshots are comparable.

## Backtest leakage — read before touching historical runs

An LLM may already know how a historical rally or crop failure ended. When
testing against historical data:
- Prefer a model whose training cutoff predates the test period, or
- Strip dates and named episodes from inputs where possible, or
- Prefer forward paper-testing over backtesting for the cleanest read.

Never treat a backtest result as trustworthy without one of the above.

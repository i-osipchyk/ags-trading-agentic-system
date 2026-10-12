# Ags Trading Analyst — Multi-Agent Pipeline

## What this project is

A twice-daily, scheduled research pipeline that produces a
bullish/bearish/neutral call with a written thesis for a fixed basket of
agricultural futures (traded as Pepperstone CFDs), plus a **paper
portfolio** that a manager agent scales up or down based on those calls.
It is a **research and evaluation tool, not an execution system** — nothing
sends an order to a broker. The point is to find out, with an auditable
track record, whether AI-assisted fundamental and technical synthesis adds
anything beyond a trend-only signal, and whether an LLM sizing those calls
adds anything beyond a mechanical sizer.

Full design writeup (architecture diagram, agent I/O schemas, rollout plan):
https://claude.ai/artifact/3rEgJWBSqQh6ZtERuy37dC — the in-repo
`docs/DESIGN.md` is the current version and wins where the two differ.

Implementation-level technical architecture (runtime, data layer, external
sources, logging format, AWS deployment, and everything else *how*-shaped):
`docs/ARCHITECTURE.md`. Build order: `docs/IMPLEMENTATION_PLAN.md`.

## Architecture

Per commodity, every cycle:

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

Once per cycle, across all seven commodities:

- **Portfolio manager** — reads the seven coordinator calls, the current
  paper book (lots per symbol), and code-computed risk facts (ATR, $ value
  of 1 ATR per lot, equity, free margin); returns a signed lot delta per
  symbol. **Deterministic guardrails** then clamp or reject its proposal
  before anything touches the paper book.
- **Shadow book** — a fixed mechanical sizer run on the same calls, through
  the same guardrails and costs. The manager is scored on its P&L *minus*
  the shadow book's — the only clean read on whether the LLM sizer earns
  its place.

On its own schedule:

- **Auditor** — grades each call at 1/2/4/8 weeks (plus a 1-day diagnostic
  checkpoint) against price and against each named driver.

The four analysts run **in parallel and blind to each other** — this is
load-bearing, not incidental. Do not give them visibility into each other's
output or the coordinator's running view; that's what keeps their signals
independent and lets the auditor attribute accuracy per agent. The same
applies one level up: **analysts and coordinators never see the paper
book.** A coordinator that knows "we're long and down 3%" starts defending
the position instead of calling the market.

## Hard constraints — do not violate these

- **No live execution.** Agents may propose position changes, and code may
  apply them **only to the paper book** (S3-backed, see `ARCHITECTURE.md`).
  Nothing in this codebase sends an order to a broker. The book sits behind
  a cTrader-shaped `Book` interface whose only implementation is
  `PaperBook`; a live implementation is out of scope until the user
  explicitly decides otherwise. Enforced by a test that fails if any cTrader
  trading message (`ProtoOANewOrderReq`, `ProtoOAAmendPositionSLTPReq`,
  `ProtoOAClosePositionReq`, …) is referenced anywhere in `src/`. If a task
  looks like it's adding live execution, stop and confirm with the user
  first.
- **Point-in-time data only.** Every data-access function takes an `as_of`
  timestamp and must not return information that wasn't public as of that
  timestamp. This is the #1 way this kind of backtest silently breaks —
  when adding a new data source, check whether it's PIT-safe (an archived,
  as-released feed) or a "latest revised values" feed (USDA PSD Online,
  NASS Quick Stats — do not use these for backtesting; use ESMIS archives
  instead for USDA reports). With two cycles a day, `as_of` must be
  timestamp-precise, not just a date.
- **Structured output only.** Every agent returns fixed schema fields
  (see the design doc), never free text. Free-form output can't be scored
  or compared cycle over cycle.
- **Deterministic where possible.** Trend, volatility, ATR, lot/notional
  arithmetic, guardrails, fills, swap, margin and stop-outs are computed by
  code, not by an LLM. Keep the LLM's job to synthesis and judgment calls
  (e.g. seasonality alignment, how much to allocate where), not
  recomputing things code already knows.
- **Fixed run shape.** Same four analysts, same commodity universe, same
  single portfolio-manager stage, every scheduled cycle. Comparability
  across cycles is what the auditor's per-agent scorecard depends on —
  don't make the fan-out adaptive without updating the design doc first.
- **One writer to the official record.** S3 is the source of truth; local
  is a pull-only mirror. Only the scheduled AWS cycle writes official runs,
  and only the portfolio Lambda writes the paper book.
- **Everything logged**, keyed by `run_id` (per commodity) and `cycle_id`
  (per portfolio stage): prompts, tool calls, tool results, outputs, raw
  manager proposals, guardrail violations, and fills. Any run must be
  replayable.

## Commodity universe

Corn, soybeans, wheat, sugar #11, coffee "C", cotton, cocoa — chosen for
liquidity and free point-in-time sources. Corn/soy/wheat move together, so
don't treat the basket as more independent signals than it is (the
guardrails cap them as one grain bucket).

## Run cadence

Twice per weekday at fixed times — **16:00 and 04:00 ET, Monday–Friday**,
no weekend runs — triggered by EventBridge Scheduler on AWS. The coordinator
still makes a single **1-week-horizon** call; consecutive calls overlap, so
scorecards must weight them (see `DESIGN.md`) rather than count every call
as an independent sample.

## Platform (`~/dev/platform`)

This project follows the platform conventions (`../CLAUDE.md`, `../platform/docs/`); only the
ags-specific parts are here.

- **LLM:** every model call goes through llmkit from `src/ags/llm/client.py`, the only module
  that imports `llmkit`/`openai`. Bind the client to the agent name as the `component`
  (`bind(client, "<agent>", commodity=..., run_id=...)`); the root `LLMClient` refuses to
  complete unbound. Run logs remain the audit trail; llmkit adds usage and cost
  (`usage.cost_usd` in the run log comes from it — don't add a second price table).
- **Secrets:** SSM SecureStrings at `/ags/<env>/<kebab-name>`, pushed with
  `uv run python scripts/put_secrets.py --env live` (`--include-tokens` to re-seed the cTrader
  tokens, which `token-refresh` rotates itself). `config.load_env()` loads them; never put
  values in Terraform.
- **One env, `live`** — a deliberate deviation from the platform's default `demo,live`: this is
  a paper forward test with no real money to stage for, and a second deployed env would be a
  second official record and double the LLM spend. AWS names are `ags-live-<name>`.

## Deployment

Terraform in `infra/` on the platform `lambda-app` / `data-bucket` modules (pinned to a platform
tag), plus Step Functions and the cycle schedule in `infra/cycle.tf`. Region from `AWS_REGION`,
default `eu-west-1`. Every push runs CI (platform `python-ci.yml`); after CI passes on `main`,
Deploy (platform `lambda-deploy.yml`) builds the image, plans, waits for the required reviewer
on the `live` GitHub Environment and for no running cycle, then applies. Never run
`terraform apply` locally. The data bucket keeps `prevent_destroy` (from the platform module) —
don't remove that guard. `tests/test_infra.py` pins the infra invariants.

## Backtest leakage — read before touching historical runs

An LLM may already know how a historical rally or crop failure ended. When
testing against historical data:
- Prefer a model whose training cutoff predates the test period, or
- Strip dates and named episodes from inputs where possible, or
- Prefer forward paper-testing over backtesting for the cleanest read.

Never treat a backtest result as trustworthy without one of the above.

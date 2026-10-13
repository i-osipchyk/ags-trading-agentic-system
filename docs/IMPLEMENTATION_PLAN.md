# Implementation Plan

Staged build order for the pipeline described in `CLAUDE.md` and
`DESIGN.md`, built to the technical decisions in `ARCHITECTURE.md`. Staged
bottom-up so each piece is independently testable — usually via a unit
test, occasionally a manual smoke run — before the next stage is allowed to
depend on it. Nothing added at any stage grants execution capability; every
stage stays within a single analyst, tool, or logging component.

A stage is not started until the previous stage's tests pass.

## Stage 0 — Scaffolding

`pyproject.toml` + `uv.lock`, the `src/ags/` package skeleton per
`ARCHITECTURE.md`'s repo layout, `config.py` (typed, fails fast on a
missing required env key), `.env.example`.

**Test:** `uv sync` succeeds; a unit test asserts `Config` raises on a
missing required key.

## Stage 1 — Logging layer (`logging/`)

`run_id` generation, JSONL writer/reader, the
`{seq, agent, event_type, payload, timestamp}` schema.

**Test:** unit test writes a fake run's events and reads them back
byte-identical; confirms append-only behavior.

## Stage 2 — PIT store core (`tools/pit_store.py`)

`read_pit(source, report, symbol, as_of)`, the single chokepoint every
source integration routes through, and the
`data/{source}/{report_type}/{symbol}/{release_date}.json` storage layout —
built against fixtures only, no real source yet.

**Test:** the fixture suite specified in `ARCHITECTURE.md` — future-dated
releases never returned, inclusive `release_date == as_of`, correct
handling when a later revision of an earlier `release_date` exists. This is
the highest-leverage stage to get right before anything depends on it.

## Stage 3 — Deterministic computation

Hand-written pandas/numpy `get_trend_state`, volatility, and seasonality
functions — no TA library, per `ARCHITECTURE.md`.

**Test:** unit tests against fixture price series with known trend/vol
characteristics. No LLM, no I/O.

## Stage 4 — First real source: prices (`tools/sources/prices.py`)

cTrader Open API, wrapped with `crochet`, feeding `get_prices` through
`read_pit`. (`get_curve` was dropped during Stage 6 — see the
`ARCHITECTURE.md` correction — cTrader has no multi-month contracts to
build a term structure from.)

**Test:** integration test against real credentials pulling one symbol;
verify the fetch-through-cache write, and that a second call for the same
`release_date` is a no-op.

## Stage 5 — LLM access layer (`llm/`)

Hand-rolled tool-calling loop against the OpenAI-compatible chat
completions API; 5-tool-call-per-agent cap enforcement; `degraded` and
`tool_call_cap_hit` flag logic.

**Test:** unit test with a stub model + fake tool verifying the cap forces
a tools-disabled final call on the 6th tool-call attempt. Separate smoke
test against the real DeepSeek endpoint with one trivial tool.

## Stage 6 — First full agent: Technical analyst

Wires Stages 3–5 into `agents/technical.py`, with its declared tool
allowlist and output schema.

**Test:** standalone run for one symbol, verify schema conformance; force a
source failure and confirm `degraded: true` surfaces rather than the run
aborting.

## Stage 7 — Remaining three analysts, one at a time

Each independently testable the same way as Stage 6:

- **7a. News** — `tools/sources/news.py` (Tavily `search_news`,
  `fetch_document`)
- **7b. Weather/season** — `tools/sources/weather.py` (US Drought Monitor +
  NOAA CPC)
- **7c. Supply/demand** — `tools/sources/usda.py` (ESMIS archive first;
  UNICA/Conab/ISO/ICO/ICCO/ICAC added lazily per-commodity in Stage 13)

## Stage 8 — Orchestrator without coordinator

`scripts/run_pipeline.py` fanning the four analysts out via `asyncio.gather`,
writing the full JSONL log, printing raw per-analyst outputs — no
synthesis yet.

**Test:** one dry run for corn; inspect the log to confirm all four
analysts saw the same `as_of` and none referenced another's output.

## Stage 9 — Coordinator (no track record yet)

`agents/coordinator.py`, the structured call schema, the conviction
rubric.

**Test:** unit test against a fixture set of four canned analyst outputs
(full-agreement, split, and mostly-neutral cases) to check rubric mapping.
Then a live end-to-end `scripts/run_pipeline.py` run for one commodity.

## Stage 10 — Coordinator track record

`read_track_record(symbol, lookback_weeks)`, a read-only join over
`logs/` + `audits/`, replacing `read_thesis_log`.

**Test:** unit test the join against fixture logs/audits, including the
"missing checkpoints are simply absent, never null-filled" behavior. Then
wire into the coordinator prompt and re-run Stage 9's live check.

## Stage 11 — Auditor

`scripts/run_auditor.py`, the due-checkpoint scan
(`trigger_timestamp + {1,2,4,8}w <= now`, diffed against what's already
graded), `audits/{run_id}/{weeks}w.json` writer.

**Test:** unit test the scan logic against fixture run timestamps (some
due, some not, some already graded). Then a real run against a backdated
fixture `run_id` to exercise a live grading pass.

## Stage 12 — Chat interface

Streamlit app (`scripts/run_chat.py`), scoped to one frozen `run_id`, zero tool access.

**Test:** manual browser check asking a question about a completed run;
confirm via logs that no live tool calls fired during the chat session.

## Stage 13 — Full commodity universe

Extend supply/demand and weather sources to sugar, coffee, cotton, and
cocoa (UNICA/Conab/ISO/ICO/ICCO/ICAC, added lazily as each commodity is
actually worked on, per `ARCHITECTURE.md`).

**Test:** one full run per remaining commodity, spot-check output sanity.

**Progress:**
- Done: U.S. cotton and sugar WASDE tables (sr17/sr16) parsed by
  `EsmisClient` (unit tests on fixture XML + live ESMIS integration).
  Weather needed no change: cotton/sugar states and all seven growing
  calendars were already covered.
- Done: coffee via USDA FAS "Coffee: World Markets and Trade" on ESMIS
  (PDF, world totals; `report="coffee_world_markets"`), not ICO — ICO has no
  usable public archive. Semiannual (Jun/Dec), and the archive's newest
  release as of 2026-10 is 2025-06-25, so coffee data is stale.
- Not built: cocoa (ICCO Quarterly Bulletin pages: dated and archived, but
  layout varies by issue and the newest issue withholds current-season
  figures) and non-US sugar/cotton (UNICA/ISO/Conab/ICAC). Until then, cocoa
  supply/demand returns `latest: None` and the analyst reports
  `degraded: true`.
- Not run: the full live pipeline run per commodity (needs the LLM and
  cTrader credentials).

## Stage 14 — Go live

**Superseded by Part 2.** The Friday manual-trigger cadence ran as a pre-v3
trial; those runs are archived (Stage 31) and don't count toward the
2-month review. Go-live is now Stage 32.

---

# Part 2 — Scheduled cycles, paper portfolio, AWS (DESIGN v3)

Builds the v3 additions from `DESIGN.md` / `ARCHITECTURE.md`: timestamp
`as_of`, a storage seam with an S3 backend, the cTrader-shaped paper book,
guardrails, the shadow sizer, the portfolio manager agent, Lambda handlers,
Telegram, Terraform, and CI. Same rules as Part 1: bottom-up, a stage is not
started until the previous stage's tests pass, and nothing grants live
execution — Stage 15 puts the test that enforces that in place before any
portfolio code exists.

Everything through Stage 25 runs locally with no AWS account involved. AWS
appears only from Stage 26 onward.

## Stage 15 — No-live-execution guard

`tests/test_no_live_execution.py`: scans `src/` for cTrader trading message
names (`ProtoOANewOrderReq`, `ProtoOAAmendPositionSLTPReq`,
`ProtoOAClosePositionReq`, `ProtoOACancelOrderReq`, `ProtoOAAmendOrderReq`).

**Test:** passes on the current tree; a temporary file referencing
`ProtoOANewOrderReq` under `src/` makes it fail (checked by hand, then
removed).

## Stage 16 — Timestamp-precise `as_of`

Move every data-access function, `read_pit`/`write_pit`, the analysts, the
coordinator, the auditor, and `run_pipeline` from `as_of: date` to a
timezone-aware `as_of: datetime`. Per-source release times in config
(date-only sources treated as released at their known time, else end of
day). Daily bars: completed bars only.

**Test:** extend the PIT fixture suite — same-day release before vs. after
`as_of`; a date-only release at its configured time; a 04:00 ET `as_of`
never sees that day's partial daily bar. All existing tests updated and
green. One live `scripts/run_pipeline.py` run to confirm nothing regressed.

## Stage 17 — Storage seam (`storage/`, local only)

`Storage` protocol (`get`, `put`, `put_if_absent`, `list`) and
`LocalStorage`. Route `pit_store`, `RunLog`, audit writes, the auditor scan,
and `read_track_record` through it. CLIs default to `LocalStorage` rooted at
`local/`; the existing UI keeps reading `logs/` / `audits/` directly.

**Test:** no behavior change — the full existing suite passes unchanged
against `LocalStorage`; new unit tests for `put_if_absent` no-op semantics.

## Stage 18 — `S3Storage`

Same protocol against one bucket. `put_if_absent` via conditional writes
(`If-None-Match: *`). `RunLog` on S3: buffer to `/tmp`, upload at end of run
and in a `finally` on failure.

**Test:** the Stage 17 storage contract tests run against both backends
(S3 via `moto`, added as a dev dependency); two concurrent `put_if_absent`
calls on one key → exactly one write; a run that raises mid-way still
uploads its partial log.

## Stage 19 — cTrader market data for the book

Extend `tools/sources/prices.py` (read-only messages only): symbol specs
(`ProtoOASymbolByIdReq` — lot size, volume min/step/max, swap long/short,
swap type, triple-swap day; margin rate from the account's leverage data),
a bid/ask quote snapshot (`ProtoOASubscribeSpotsReq` → first tick →
unsubscribe), and H1 trendbars. All stored PIT through `read_pit`: specs
keyed by fetch date, quotes and H1 bars by capture timestamp.

**Test:** unit tests on fixture protobuf responses; integration test pulls
specs, a quote, and a day of H1 bars for one symbol and checks the
fetch-through-cache write. Record the real specs for all seven symbols —
this is where you learn whether any minimum lot is too big for a $10k book.

## Stage 20 — Risk facts (`portfolio/risk.py`)

ATR (14-day, completed daily bars), `atr_usd_per_lot` (ATR × contract size
× $/point, from specs), notional per lot.

**Test:** unit tests against fixture bars + specs with hand-computed values.
No I/O, no LLM.

## Stage 21 — Paper book (`portfolio/book.py`, `paper_book.py`)

cTrader-shaped `Position` / `Fill` / `BookState` models and the `Book`
protocol; `PaperBook` with fills at bid/ask, volume rounding (toward zero),
swap accrual at rollover (triple-swap day included), used margin, equity,
peak equity, and the stop-out replay over H1 bars (adverse extreme,
largest-loss position closed first). State in and out as one cycle-file
dict; no storage I/O inside the class.

**Test:** table-driven unit tests — open/add/reduce/flip/close; swap over a
weekend and over the triple-swap day; a drawdown that crosses the stop-out
level mid-replay closes the right position at the right price; replaying a
sequence of cycle files reproduces the final equity exactly.

## Stage 22 — Guardrails (`portfolio/guardrails.py`)

`apply_guardrails(...)` as specified in `ARCHITECTURE.md`: the nine rules
in order, rejects before clamps before portfolio scaling, rounding last,
violations as `{rule, symbol, proposed, applied, detail}`. All limits from
config.

**Test:** one table-driven case per rule plus interactions — a neutral leg
stays frozen through any proposal but shrinks under the gross cap; a sign
flip against a bullish call clamps to flat without touching other legs; a
schema failure holds everything; the breaker allows reductions only;
rounding never pushes a leg back over a cap; a symbol whose minimum volume
exceeds its cap is reported, not rounded up.

## Stage 23 — Shadow sizer (`portfolio/shadow.py`)

`target_lots = sign(call) × conviction/5 × risk_budget × equity /
atr_usd_per_lot`, neutral held, `delta = target − current`.

**Test:** unit tests on fixture calls/books, including a null (failed)
commodity and a neutral leg.

## Stage 24 — Portfolio manager agent (`portfolio/manager.py`)

Prompt, input assembly (seven calls, lots, ATR, `atr_usd_per_lot`, equity,
free margin — nothing else), output schema `{legs: [{symbol, delta_lots,
rationale}], portfolio_note}`, parsed and validated by code. Runs through
the existing LLM loop with **no tools**.

**Test:** stub-model unit tests — valid output parses; missing symbol /
non-numeric delta is a schema failure that guardrail 1 turns into
hold-all; the prompt contains no raw analyst output and no track record.
Then one live call against canned inputs, schema-checked.

## Stage 25 — Portfolio stage + local cycle

`portfolio/stage.py` `run_portfolio(cycle_id, calls, storage, …)`: load both
books' last cycle files, fetch market data, replay, mark, run manager and
shadow through guardrails, fill, write `portfolio/{book}/cycles/{cycle_id}.json`
and `portfolio/logs/{cycle_id}.jsonl`. `off_schedule=True` logs everything
but writes no cycle file. Breaker reset read from an injected source (SSM in
Lambda, a local file in tests). `scripts/run_cycle.py`: seven pipelines + portfolio
stage locally, into `local/`. `portfolio/metrics.py`: equity curves,
manager-vs-shadow P&L.

**Test:** stage-level unit test with stub model, stub prices, and
`LocalStorage` — two consecutive cycles, one with a failed commodity (held),
one with a stop-out in the replay window; off-schedule writes nothing to the
books. Then one live `scripts/run_cycle.py` into `local/` and a read-through of the
cycle files.

## Stage 26 — Auditor additions

1-day diagnostic checkpoint (`DIAGNOSTIC_CHECKPOINT_DAYS`, `1d.json`,
`checkpoint_days`, due on the first completed daily bar after the call),
included by `read_track_record`, invisible to the UI's `CHECKPOINT_WEEKS`
loop. Effective-count weighting (`1/k` overlapping same-commodity calls) in a
scorecard helper used by the digest and the review.

**Test:** scan unit tests for 1-day due-ness (Friday 16:00 call → Monday
close); UI `build_grid` output unchanged with `1d.json` files present;
effective-count weights on a fixture of ten overlapping calls.

## Stage 27 — Lambda handlers + container image

`handlers/` — `commodity_pipeline`, `portfolio`, `auditor`, `digest` —
thin wrappers that build `Config` from the environment
(`config.load_env()`: SSM `/ags/<env>/*` on Lambda) and `S3Storage` from
env, then call existing functions. Every LLM call goes through llmkit with
the agent as `component` (`llm/client.py`); handlers flush it after each
invocation.
`crochet.setup()` once at import. Late-start (>1 h) abort. Move Streamlit to
a `ui` dependency group (`default-groups` keeps local unchanged).
`Dockerfile` on the AWS Lambda Python base image, arm64.

**Test:** unit tests for each handler with fakes (late-start abort, failed
commodity passed through as `null`). Build the image and invoke
`commodity_pipeline` locally via the Lambda Runtime Interface Emulator
against `moto`/`LocalStorage`.

## Stage 28 — cTrader token refresh (superseded: now on the platform)

Originally an ags `token_refresh` Lambda. Replaced by the account's shared
pair in `/platform/shared/ctrader-*`, refreshed by the platform's
`platform-ctrader-token-refresh` (platform conventions § Secrets). What
stays here: Lambdas re-read SSM per invocation and once more on an auth
error, and the shared value wins over any stale `/ags/<env>/` copy.

## Stage 29 — Telegram alerts + digest (`notify/`)

Telegram client, alert functions (commodity failed twice in a row, breaker
tripped; token refresh failed, cycle failed and Lambda errors arrive through
the shared `platform-alarms` topic), and the 17:00 digest: spend (LLM by
agent/commodity from llmkit's per-call cost in the run logs, 24 h + MTD; AWS
from Cost Explorer, prior day), book, manager vs shadow, latest calls.

**Test:** unit tests render the digest from fixture cycle files and run
logs (golden-text comparison); fake HTTP for Telegram; consecutive-failure
detection across fixture cycles. One real message to the chat.

## Stage 30 — Terraform + CI (on the platform)

Built on `~/dev/platform` (`platform/docs/adoption.md` § ags). The state
bucket, GitHub OIDC, the `gh-deploy-ags` role, the `ags` ECR repo and the
`platform-alarms` topic come from the platform **bootstrap** — not from this
repo.

- `infra/`: the platform `data-bucket` and `lambda-app` modules (pinned to a
  platform tag) for the bucket, the four Lambdas, their role, log groups,
  schedules (digest 17:00, auditor 18:00, both
  `America/New_York`) and Errors alarms; next to them, Step Functions
  `ags-<env>-cycle` (Map ×7 with retry/catch → portfolio) with its log group,
  the cycle schedule `cron(0 4,16 ? * MON-FRI *)` and its own scheduler role,
  the SSM breaker parameter and the cycle-failed alarm. Region from
  `AWS_REGION`, defaulting to eu-west-1. One env: `live`.
- Secrets: SSM SecureStrings at `/ags/live/*`, pushed by
  `scripts/put_secrets.py`; never in Terraform.
- `.github/workflows/ci.yml` → platform `python-ci.yml`;
  `.github/workflows/deploy.yml` → platform `lambda-deploy.yml` for `live`
  after CI on `main`, with the `live` Environment's required reviewer, and
  `scripts/wait_for_cycle.sh` as `pre-apply` (no `RUNNING` executions).

**Test:** `tests/test_infra.py` pins the invariants; `terraform validate`
locally; the first deploy's plan (job summary) creates only `ags-live-*`
resources; a manual off-schedule execution completes end to end and writes
no book cycle files; a planned destroy of the data bucket fails on
`prevent_destroy` (checked with a throwaway plan, not applied).

## Stage 31 — Data migration + local mirror

Upload the existing PIT store (`data/`) to the bucket — the cache is still
valid and saves refetching. Move pre-v3 `logs/` and `audits/` to
`archive/pre-v3/` so they stay out of the new track record.
`scripts/sync_local.sh` (pull-only into `data/`, `logs/`, `audits/`,
`portfolio/`). Gitignore `portfolio/` and `local/`.

**Test:** sync down onto a clean checkout; the unchanged UI opens and shows
the official runs; `read_track_record` in a fresh cycle sees no pre-v3 runs.

## Stage 32 — Go live

Let the first scheduled cycle fire. Its date starts the 2-month clock —
record it in `DESIGN.md`'s Review checkpoint. Watch the first few digests
for spend, near-duplicate calls, and guardrail activity; tune limits only
through config, and log any change in `DESIGN.md` with its date so the
review can see what was in force when.

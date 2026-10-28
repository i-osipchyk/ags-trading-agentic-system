# Technical Architecture Decisions

Implementation-level decisions for the pipeline described in `CLAUDE.md` and
`docs/DESIGN.md` (v3). Those two files define *what* the system does and the
guardrails it must respect; this file pins down *how* it's built. Reached via
a grilling session on 2026-09-30, extended by a second one on 2026-10-12
(scheduled AWS cycles, portfolio stage, paper book, Terraform/CI) — treat as
settled unless something concrete forces a revisit (not before the 2-month
review, per the design doc's Review checkpoint). Sections from "Scheduled
cycles on AWS" onward are v3 additions; earlier sections are amended in place
where v3 changed them.

## Runtime

- **Python + asyncio.** The four analysts run as independent coroutines
  fanned out with `asyncio.gather`, not a multi-agent framework
  (LangGraph, CrewAI, etc.) — those frameworks assume flexible
  agent-to-agent messaging, which is exactly what the fixed-run-shape and
  blind-analyst guardrails forbid.
- **Dependency management:** `uv`, with `pyproject.toml` + `uv.lock`.
- **Package manager rationale:** reproducible runs matter here specifically
  because "any run must be replayable" — a lockfile pins the code version
  alongside the logged `run_id`.

## Repo layout

```
src/ags/
  agents/            # one file per agent: prompt + output schema + tool allowlist
    technical.py
    news.py
    weather.py
    supply_demand.py
    coordinator.py
    auditor.py
  tools/             # tool implementations, never agent logic
    pit_store.py     # read_pit(source, report, symbol, as_of) — the PIT chokepoint
    sources/
      usda.py        # ESMIS archive scrape/parse
      unica.py       # Brazilian sugar/ethanol
      conab.py       # Brazilian crop supply
      iso.py         # International Sugar Organization
      prices.py      # cTrader Open API, crochet-wrapped
      weather.py     # US Drought Monitor + NOAA CPC
      news.py        # Tavily
      cot.py         # CFTC positioning archive
      # ico.py / iccо.py / icac.py added lazily, per commodity, as needed
  llm/               # hand-rolled tool-calling loop, model-agnostic client
  logging/           # run_id / cycle_id generation, JSONL writer/reader
  storage/           # Storage protocol: LocalStorage, S3Storage (v3)
  portfolio/         # v3: no LLM code except manager.py
    book.py          # Book protocol + cTrader-shaped Position/Fill models
    paper_book.py    # PaperBook: fills, swap, margin, stop-out replay
    demo_book.py     # DemoBook: manager's book on the Pepperstone demo account (state read from the broker)
    demo_gateway.py  # CTraderDemoGateway: the only module that sends orders; demo host only
    specs.py         # symbol specs (lot size, volume min/step/max, swap, margin rate)
    risk.py          # ATR, atr_usd_per_lot, notional
    guardrails.py    # pure function: proposal → applied deltas + violations
    shadow.py        # mechanical sizer
    manager.py       # manager agent: prompt + output schema (agents/ style)
    stage.py         # run_portfolio(cycle_id): the whole portfolio stage
    metrics.py       # equity curves, manager-vs-shadow P&L, spend rollups
  notify/            # Telegram client, alerts, daily digest (v3)
  handlers/          # Lambda entry points — thin wrappers, no logic (v3)
  ui/                # Streamlit app: dashboard (dashboard, dashboard_page), runs grid + job control (runs, jobs, runs_page), chat (chat, streamlit_app)
scripts/                 # every CLI and helper script
  run_pipeline.py        # one commodity run (local, unofficial namespace)
  run_cycle.py           # full cycle locally — 7 pipelines + portfolio stage (v3, unofficial)
  run_auditor.py         # trigger due checkpoint evaluations (all, or one via --run-id/--weeks)
  run_chat.py            # Streamlit app: dashboard (default), runs grid, chat over frozen runs
  sync_local.sh          # pull-only S3 → local mirror (v3); optional prefix args (the dashboard pulls portfolio logs attempts)
  put_secrets.py         # .env → SSM /ags/<env>/* (v3)
  wait_for_cycle.sh      # deploy pre-apply: wait for no RUNNING cycle (v3)
  migrate_to_s3.py       # one-off: seed the bucket from a pre-v3 checkout (v3)
infra/                   # Terraform (v3): platform lambda-app + data-bucket modules, Step Functions
  env/live.tfvars        # the one environment
Dockerfile               # Lambda container image (v3)
.github/workflows/       # CI on every push; deploy to live after CI on main (v3, platform workflows)
tests/
  tools/               # PIT fixture-based unit tests
data/                   # PIT store contents (gitignored; mirror of S3 in v3)
logs/                    # run JSONL logs (gitignored; mirror of S3 in v3)
jobs/                    # UI job lock, output tail, exit code (gitignored)
audits/                  # audit JSON files (gitignored; mirror of S3 in v3)
portfolio/               # paper-book cycle files + portfolio logs (gitignored; mirror of S3)
local/                   # unofficial local runs — never synced up (gitignored, v3)
.env / .env.example
```

Each `agents/*.py` file declares which `tools/*` functions that agent may
call (its tool allowlist) — never the tool implementation itself, so the
"which agent can see which data source" boundary is visible at a glance.

## LLM access layer

- **Hand-rolled tool-calling loop** against an OpenAI-compatible chat
  completions API — not an agent SDK (Anthropic's, OpenAI Assistants/Agents
  SDK, LiteLLM's agent layer, etc.).
- Works against DeepSeek V4.1 Flash today (per design doc, same model for
  every agent for now); swapping models or per-agent tiering later is a
  `base_url`/model-string change, not a redesign.
- The 5-tool-call cap, `degraded` flagging, and full prompt/tool-call/result
  logging are guardrails owned by this loop, not delegated to a framework.
  A tool that raises (or an unknown tool name, or unparseable arguments) is
  returned to the model as `{"error": "<Type>: <message>"}` and still counts
  toward the cap, so one bad tool can't degrade the whole agent.
- **Every model call goes through llmkit** (the `~/dev` platform's tracked
  client, `platform/docs/llm-observability.md`), from one module:
  `llm/client.py`. The root `LLMClient` has no `complete`; it must be
  `bind()`-ed to a **component** first — the agent name (`technical`, `news`,
  `weather`, `supply_demand`, `coordinator`, `manager`, `auditor`,
  `run_chat`), with `commodity` / `run_id` / `cycle_id` as metadata. The
  binding happens where the agent name is already known (`LoggingChatClient`,
  the auditor, the run chat), so `run_loop` and the agents are unchanged.
  Each pipeline run, portfolio stage and audit checkpoint is one llmkit
  trace. llmkit prices every call from the platform price table and sends it
  to its sinks (JSONL locally, stdout → CloudWatch on Lambda, Langfuse when
  keyed); the price also goes into the run log's `usage.cost_usd`. Run logs
  stay the full audit trail; llmkit adds only usage and cost tracking.

### Tool-call-cap enforcement

- The loop counts tool calls per agent per run. After the 5th tool result
  returns, it forces one more model call with tools disabled, producing a
  final structured answer instead of another tool request.
- That output carries a new flag, **`tool_call_cap_hit: true`**, distinct
  from `degraded` (which signals stale/failed sources, not research cut
  short by the cap).
- The run is **not** treated as failed or incomplete — it proceeds to the
  coordinator normally, which can weight or discount that analyst's input
  using the flag.

## Point-in-time data layer

- **Storage layout:** `data/{source}/{report_type}/{symbol_or_region}/{release_date}.json`
  — one immutable file per `(source, report, release_date)`. Fetch code
  refuses to overwrite an existing file; a re-fetch of an already-stored
  release_date is a no-op.
- **Enforcement chokepoint:** every `get_*` tool routes through a single
  shared helper, `read_pit(source, report, symbol, as_of)`, which filters
  to `release_date <= as_of` (inclusive) before returning anything. This is
  the one place PIT logic can break — every source integration reuses it
  rather than reimplementing filtering.
- **`release_date` vs `fetched_at`:** both are stored on each file, but
  filtering happens on `release_date` (when the report says its data is as
  of / was published), never `fetched_at` (when the code happened to pull
  it). A report fetched late still respects its original release_date.
- **Ingestion model: fetch-through-cache, on-demand, at agent run-time.**
  `get_usda_report(report, as_of)` (and equivalents) checks the local store
  first; if the latest qualifying release isn't present, it fetches live,
  writes it immutably keyed by the release_date embedded in the fetched
  report, then returns it. No separate scheduled ingestion job — the store
  grows as a point-in-time archive "for free," useful if historical
  backtesting is added later.
- **Testing:** a dedicated fixture-based unit test suite isolated to
  `read_pit` and the fetch-through-cache path — deliberately future-dated
  releases mixed into fixtures, asserting they never come back; inclusive
  `release_date == as_of`; correct handling when a later revision of an
  earlier release_date exists. No end-to-end PIT testing through a live
  agent run — the leakage risk lives entirely in `read_pit`.

### Timestamp-precise `as_of` (v3)

Built with `as_of: date`, which was enough for one run a week. With 04:00 and
16:00 ET cycles on the same day, a date is ambiguous — both cycles would read
the same `as_of`, and a report released at 12:00 ET would be inside the 04:00
cycle's window on replay. v3 moves every data-access function to a
timezone-aware `as_of: datetime`:

- `read_pit` filters on a release **timestamp** (`release_date` stays for
  sources that only publish a date; those are treated as released at a
  per-source configured time, e.g. WASDE 12:00 ET, COT 15:30 ET, and at
  end-of-day when unknown — conservative, never early).
- Daily price bars: only **completed** bars are visible at `as_of`. The 04:00
  cycle never sees the in-progress session as a close.
- Quotes and intraday bars (M15, M30, H1) are new PIT objects keyed by capture timestamp; a call
  asking for a longer look-back than an earlier capture backfills the older part. A live cycle
  fetches them on demand (the technical analyst's `get_prices(tf=...)`); a replay only reads
  what was captured by its `as_of`. The pipeline's
  reference quote is one of them, captured at pipeline start and only for a live cycle (within
  one hour of the scheduled time); a replayed run gets none and keeps the last-close price.
- Cycles are 06:00 and 12:30 ET (all Pepperstone ags markets open; see DESIGN.md). The stage
  re-captures execution quotes after the manager's model call, so both books trade on the same
  fresh quotes, and records `reference_prices`, `drift`, and `costs.drift`.
- `run_id` was already timestamped; `as_of` is the cycle's trigger timestamp.

The fixture suite gains intraday cases: same-day release before vs after
`as_of`, and the partial-daily-bar case.

## Deterministic computation

- Trend, volatility, and similar "facts" (per the design doc's requirement
  that these be code-computed, not LLM-derived) are **hand-written
  pandas/numpy functions**, not a TA library (`ta-lib`, `pandas-ta`).
  `ta-lib`'s C-extension install is real friction for a project that
  should stay easy to run locally; neither library naturally returns
  "state + age since state changed," which `get_trend_state` needs.
- Unit-tested with fixture price series, same pattern as the PIT tests.

## Logging and replay

- **Format:** append-only JSONL, one file per run: `logs/{run_id}.jsonl`.
  `run_id` = `{commodity}_{trigger_timestamp}` (e.g.
  `corn_2026-09-11T18-30-00Z`). Each line:
  `{seq, agent, event_type: prompt|tool_call|tool_result|output, payload, timestamp}`.
- **Replay = deterministic reconstruction, never live re-execution.**
  "Replayable" means the log alone lets you reconstruct exactly what each
  agent saw and said, by replaying recorded tool results — never by
  re-hitting the model API or external sources live (which would risk PIT
  drift from revised sources and burn cost for no auditing benefit).
  Re-running the coordinator and manager with a *different* model or prompt
  is a separate thing: see § Counterfactual replay.
- The log is the source of truth for both the auditor's frozen inputs and
  the chat interface's "no live tool calls" constraint.
- **Cycles (v3):** `cycle_id` = `cycle_{trigger_timestamp}`. The seven
  commodity `run_id`s in a cycle share its trigger timestamp, so they group
  without a lookup. The portfolio stage logs to
  `portfolio/logs/{cycle_id}.jsonl` — deliberately **outside** `logs/`, so
  the auditor's scan and the unchanged UI grid never mistake it for a
  commodity run. Same event schema, plus event types `proposal_raw`,
  `proposal_applied`, `violations`, `fills`.
- **On S3 (v3):** S3 has no append. In a Lambda, `RunLog` writes JSONL to
  `/tmp` as today and uploads the file once, at the end of the run and from
  a `finally` block on failure — so a crashed run still leaves its log.

## Trigger and UI

**v3 amendment:** the official trigger is now the scheduler (see "Scheduled
cycles on AWS"). The CLIs stay, but write to the unofficial `local/`
namespace. The UI is **deliberately unchanged** in v3: it reads the
pull-only mirror at the same `logs/` / `audits/` paths as before. Its run
buttons still work but write to `local/`, so their output doesn't appear in
the grid — a known gap until the UI is revisited. The "off-cadence" flag
still says "not Friday," which is now meaningless; also left for the UI
revisit.

- **Pipeline trigger:** synchronous CLI, `uv run python scripts/run_pipeline.py --commodity corn`.
  Generates `run_id`, `asyncio.gather`s the four analysts, passes results to
  the coordinator, writes the JSONL log, prints the final call. No web
  server, no queue, no background worker — a CLI is trivially replaced by a
  scheduled job later without redesign.
- **Chat interface:** a local Streamlit app (`uv run streamlit run scripts/run_chat.py`,
  bound to `127.0.0.1` via `.streamlit/config.toml`) — not FastAPI/Jinja, not a
  JS framework/SPA. Scoped to a single frozen `run_id` chosen in the sidebar
  (newest first; the conversation resets when the run changes); reads only
  that run's JSONL log; makes a fresh model call per question but with zero
  tool access (`tools=None`), so "read-only, no live tool calls" is
  architecturally enforced, not just documented. Chat history is held in
  Streamlit session state only and is never written to the run log or the
  audited record. The logic lives in `ags.ui.chat.answer_question` (UI-agnostic)
  and `ags.ui.streamlit_app.render_chat_page`.
- **Runs page and job control:** the app's default page is a grid of runs
  (filter by commodity and trigger date) with one cell per audit checkpoint:
  yellow = not yet due, red = due and ungraded, green = graded (✓/✗/– for
  `direction_correct`), grey = run has no call. Clicking a run opens the chat
  page. The page can also trigger work, but the **CLI remains the trigger**:
  buttons spawn `scripts/run_pipeline.py` / `scripts/run_auditor.py` as detached subprocesses
  (no queue, no worker). Exactly one job runs at a time, tracked in
  `jobs/current.json` (pid, label, start time; a dead pid is a stale lock) with
  output in `jobs/output.log` and the exit code in `jobs/exit_code`; "run all"
  runs the commodities sequentially. UI runs never pass `--as-of`, so backtests
  stay CLI-only. A run triggered on anything but a Friday (ET) is flagged
  off-cadence; weekend single runs and the bulk actions ("run all", "grade all
  due") require a confirmation click. Logic lives in `ags.ui.runs`
  (`build_grid`) and `ags.ui.jobs` (cadence, locking, command builders);
  `ags.ui.runs_page` is rendering only.
- **Dashboard:** the app's default page; the runs grid and chat stay as they were. It charts the
  paper books over time from the local mirror (`portfolio/{manager,shadow}/cycles/`, `logs/`,
  `attempts/`), on a UTC axis with All / 30d / 7d ranges: equity for both books with the
  manager − shadow gap, max and current drawdown (recomputed from the closing-equity curve over
  the range, not the book's recorded peak); signed positions per commodity
  (lots or USD, shorts negative, plus gross) for a chosen book; the coordinator's calls as a grid
  on the same time axis; margin utilization (used margin / equity, axis capped at 100%); cumulative P&L per commodity
  for a chosen book, right after equity (realized fills and stop-outs plus open P&L at the cycle's marks, so spread
  and commission are not in it); and LLM spend per UTC day (only days with a cost), stacked by commodity or agent, with the running
  total as a line on a right-hand axis over the selected range (`notify.spend.daily_spend`; retried
  attempts included, unpriced calls left out). "Pull latest data" runs
  `scripts/sync_local.sh portfolio logs attempts` through the same one-job runner. It is read-only
  and local like the rest of the app. Logic is in `ags.ui.dashboard` (pure pandas frames);
  `ags.ui.dashboard_page` is rendering only.

## Auditor

- **Trigger:** manual CLI, `uv run python scripts/run_auditor.py`, no arguments. Scans all
  logged runs, computes which `(run_id, checkpoint_weeks)` pairs are due
  (`trigger_timestamp + {1,2,4,8} weeks <= now`) by diffing against what's
  already graded, and runs a separate evaluator invocation per outstanding
  pair.
- **Storage:** `audits/{run_id}/{weeks}w.json`, one file per graded
  checkpoint — separate from the pipeline's own `logs/`, so it can be
  matched back to the originating run and horizon independently.
- Idempotent by scan: a missed week self-heals the next time the command
  runs; nothing is lost, it just grades late.
- **v3:** triggered by its own EventBridge schedule (weekdays, 18:00 ET)
  instead of by hand; same scan, same idempotency.
- **1-day diagnostic checkpoint (v3):** kept **out of** `CHECKPOINT_WEEKS`,
  in its own `DIAGNOSTIC_CHECKPOINT_DAYS = (1,)`, stored as
  `audits/{run_id}/1d.json` with `checkpoint_days: 1` instead of
  `checkpoint_weeks`. The UI iterates `CHECKPOINT_WEEKS` to draw grid
  columns, so this keeps it invisible there. `read_track_record` includes it.
  Due when the first **completed** daily bar after the call exists (a 16:00
  Friday call is graded off Monday's close, not Saturday's).

## Coordinator track record ("digital brain")

Not in the original design doc — added so the coordinator can check its own
past calls against how they graded out, and adjust emphasis accordingly
(e.g. "the driver I leaned on last time didn't play out — weight it less").

- **Mechanism:** a read-only join tool, **replacing** `read_thesis_log`:
  `read_track_record(symbol, lookback_weeks)`. Per past `run_id` in the
  window, returns `{call, conviction, thesis, key_drivers,
  supporting_analysts, dissenting_analysts}` plus whatever audit checkpoints
  exist for it (`{checkpoint_weeks, direction_correct, driver_verdicts,
  calibration_note, thesis_vs_outcome}` for each `audits/{run_id}/{weeks}w.json`
  found). No new storage, no write path — a deterministic join over
  already-logged, already-tested data. Runs whose coordinator produced no
  call (a degraded output, e.g. the model API refused the request) are
  skipped, as the auditor skips them: there is nothing to review.
- **No synthetic placeholders.** Missing audit checkpoints are simply
  absent from the response, not filled with null/pending stubs. The
  coordinator's prompt must expect variable-shaped track-record data — one
  of very few places in the system where a fixed shape doesn't apply,
  because it reflects real elapsed time, not a fixed per-run schema.
- **Scope: coordinator only, not the four analysts.** Extending
  track-record visibility to each analyst would compound the
  run-shape-comparability cost (see below) across five agents instead of
  one, before knowing the coordinator-level version is even useful. A
  natural follow-on if it proves valuable, not built now.
- **No summarization/consolidation step.** A distilled "lessons learned"
  memory (LLM-written, separately stored) was considered and explicitly
  deferred — it's a second thing to build and validate (is the summarizer
  itself accurate?) before knowing whether the raw-join version alone is
  useful. Revisit only after the 2-month review, same bucket as DB
  migration and model tiering.
- **Accepted comparability cost:** the coordinator starts cold (empty track
  record) at week 1 and accumulates naturally over the 2-month window.
  Early calls and late calls are not run under identical conditions on this
  dimension. This is an explicit, accepted caveat for the 2-month
  qualitative review, not something controlled for by delaying activation.

## External data sources

| Source | Used for | Integration pattern |
| --- | --- | --- |
| DeepSeek V4.1 Flash | All agents (model) | OpenAI-compatible API, hand-rolled loop |
| cTrader Open API | `get_prices` | Official Spotware Python SDK (Twisted-based), wrapped with `crochet` to expose an asyncio-facing interface; credentials via env vars the user provides |
| Tavily | News analyst search | LLM-shaped search API; free tier (1,000 credits/mo). Searched on the 06:00 ET cycle only (the 12:30 cycle carries the output forward, `agents/news.py`): worst case 7 commodities × 5 tool calls × ~22 weekdays ≈ 770 credits/mo, vs ≈ 1,540 if both cycles searched |
| USDA ESMIS archive | WASDE, Crop Progress, Export Sales | Scrape/parse the dated release archive directly — **not** PSD Online or NASS Quick Stats, both of which return latest-revised values rather than as-published figures |
| UNICA / Conab / ISO | Sugar & Brazilian crop supply | Scrape/parse each body's public release archive into the PIT store, same pattern as ESMIS |
| ICO / ICCO / ICAC | Coffee / cocoa / cotton supply-demand | Same scrape/parse pattern; built lazily, one per commodity, only when that commodity is actually worked on |
| US Drought Monitor | Weather analyst — drought/severity | Weekly, dated, never-revised archive — direct PIT fit |
| NOAA CPC | Weather analyst — temp/precip outlooks | Archived, dated outlook products ("as issued," not reanalysis) |
| CFTC COT archive | `get_positioning` | Weekly dated archive, same scrape/parse pattern |

**Weather scope:** US Drought Monitor + NOAA CPC are US-only. Coffee,
cocoa, and sugar (Brazil/India/West Africa-heavy) get thinner weather input
this cycle as a result. A global source is an explicit "revisit after the
2-month review" item, not built now.

**Correction to the design doc's source list:** "ISMA" (listed alongside
USDA/UNICA/Conab/ISO in both `CLAUDE.md` and the v2 design doc) does not
map to a body we could confirm exists. Dropped from the source list rather
than resolved to a guessed replacement; ICO/ICCO/ICAC cover coffee/cocoa/cotton
instead, added per-commodity as needed.

**Correction to the design doc's Technical analyst tools:** `get_curve`
(term structure / calendar spreads, via cTrader) was dropped during Stage 6.
Queried against the live account, cTrader's commodity symbol list has no
multi-month contracts — one rolling instrument per commodity, plus
margin/swap-free variants of that same instrument (`-F`, `_SB`, `_SBE`), not
different expiries. A retail CFD broker has no reason to expose a futures
ladder. Real term structure would need an exchange or paid vendor feed
(CME/ICE settlement data); revisit after the 2-month review if it turns out
to matter.

## Secrets and config

- `.env` (gitignored) + `python-dotenv`, loaded through a single typed
  `config.py` (e.g. `Config.MAX_TOOL_CALLS`, `Config.DEEPSEEK_API_KEY`,
  `Config.CTRADER_CLIENT_ID`, etc.) — not scattered `os.environ[...]` calls.
- `.env.example` checked in with no real values.
- Fails fast at startup if a required key is missing, rather than failing
  mid-run partway through the four analysts.
- **v3, in Lambda:** the same `Config`, built from `os.environ` after
  `config.load_env()` copies the SSM SecureStrings under `/ags/<env>/` (and
  the shared `/platform/shared/` keys: Langfuse and the cTrader pair) into the environment,
  kebab-case → UPPER_SNAKE (`/ags/live/deepseek-api-key` →
  `DEEPSEEK_API_KEY`; platform conventions § Secrets). Loaded at cold start
  and re-read on every invocation, so a cTrader token the platform rotated is
  picked up by warm containers. Project values are pushed by
  `scripts/put_secrets.py` from `.env`, never by Terraform. New keys:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
  Non-secret settings (guardrail limits, risk budget, starting equity,
  stop-out level, per-source release times, storage bucket) are plain env
  vars set by Terraform.
- **Guardrail and book parameters** are config, never literals in code:
  `PER_COMMODITY_CAP=1.0`, `GRAIN_BUCKET_CAP=1.5`, `GROSS_LEVERAGE_CAP=3.0`,
  `MARGIN_BUFFER=0.40`, `MIN_RESIZE_FRACTION=0.20`, `DRAWDOWN_BREAKER=0.60`,
  `STARTING_EQUITY_USD=10000`, `SHADOW_RISK_BUDGET=0.01`,
  `STOP_OUT_MARGIN_LEVEL=0.50` (verify against the Pepperstone account).

## Scheduled cycles on AWS (v3)

Region from `AWS_REGION`, defaulting to `eu-west-1` (platform conventions
§ Region) — never hard-coded anywhere else. Schedules are in
`America/New_York` regardless of region. One environment, `live` (a paper
forward test: there is no real money to stage for, and a second deployed env
would be a second official record). Names are `ags-live-<name>`.

```
EventBridge Scheduler  cron(0 4,16 ? * MON-FRI *)  tz America/New_York
  └─ Step Functions "ags-live-cycle"  (input: cycle_id, scheduled_time)
       ├─ Map ×7 (parallel) → Lambda commodity-pipeline(commodity)
       │     retry 2× with backoff; Catch → {commodity, failed: true}
       ├─ Lambda portfolio(cycle_id, commodity results)
       └─ Lambda digest  (Catch → Pass: a failed digest never fails the cycle)
Scheduler (weekdays 18:00 ET) → Lambda auditor
CloudWatch alarms (execution failed, Lambda errors) → SNS platform-alarms → Telegram
scripts/retry_cycle.py (by hand) → Step Functions, same slot, within the retry window
```

- **One container image** (ECR repo `ags`, arm64, pinned by digest), all four Lambdas, different handler
  per function. Built from the same `src/ags` package with the UI dependency
  group excluded (Streamlit moves to a `ui` dependency group; uv's
  `default-groups` keeps local `uv sync` unchanged). Zip packaging was
  rejected: pandas + numpy + Twisted + the cTrader SDK sit too close to the
  250 MB unzipped limit.
- **Timeouts:** `commodity_pipeline` 10 min (today's runs take 33–70 s
  sequentially; headroom for retries and slow sources), `portfolio` 5 min.
- **Twisted/crochet in Lambda:** `crochet.setup()` runs once per container at
  import time and the reactor is never stopped — Twisted's reactor can't be
  restarted in-process. The cTrader client connection is reused across warm
  invocations and re-established on failure.
- **Late-start rule:** a cycle whose execution starts more than 1 h after its
  `scheduled_time` aborts and alerts rather than running late — a late run
  would break comparability. The one exception is the retry of a held cycle
  (below), allowed up to `RETRY_WINDOW_HOURS` (default 3, capped at 6).
- **Missing commodity:** the portfolio Lambda receives the Map results; a
  `failed: true` commodity becomes a `null` call → guardrail 9 holds its leg.
  Two consecutive failures for the same commodity alert.
- **On-demand cycles** (`uv run python scripts/trigger_cycle.py`) create a
  one-time Scheduler schedule, a couple of minutes ahead, with the `cycle-morning`
  schedule's target copied verbatim. Scheduler fills in the scheduled-time
  placeholder, so the run takes exactly the path of a 06:00/12:30 cycle and
  **is an official cycle**: it writes both books, places the manager's orders
  when `MANAGER_BOOK=demo`, and can alert. There is no dry-run flag. Its
  `scheduled_time` is the fire time, so a run before 09:00 ET is a News-searching
  cycle and a later one carries the news forward. Use it to test the real path;
  a hand-started execution never exercises Scheduler's substitution. The
  repeat-cycle guard and `put_if_absent` keep one cycle id from trading or
  recording twice.

### Retrying a held cycle

A provider error that no retry fixes, DeepSeek 401/402 (bad key, no balance) or Tavily
401/403/432/433 (bad key, plan or pay-as-you-go limit), fails every later cycle too until
someone acts. Agents catch it like any outage (degraded output, null call) and nothing raises,
so `llm/client.py` and `tools/sources/news.py` record it in `ags/blockers.py`, per invocation.

- **Held, not written.** A commodity run that hit one returns `blocked` and appends a
  `blocked` event to its run log. If any commodity, or the manager's own calls, was blocked,
  the portfolio stage computes both books as usual but writes **neither** cycle file. Its log
  gets a `held` event, and one alert names the provider, where it hit, and the retry deadline.
  The other cycle alerts (manager failed, commodity failed twice) are skipped as the same
  problem. A held slot that isn't retried is simply missing from the book; the next cycle
  picks up from the last written one (stop-outs are replayed over the gap as usual).
- **Retry:** fix the provider, then `uv run python scripts/retry_cycle.py` (default: the
  latest slot; `--slot` for another). It starts the state machine with the slot's own
  `scheduled_time`, so cycle_id, run ids and `as_of` are unchanged. It refuses a slot that is
  already in the book, was never attempted (use `trigger_cycle.py` for a new cycle), is past
  the window, or while a cycle is running. The Lambdas enforce the same window themselves.
- **What re-runs:** a "retry" is detected from storage, not from the input. A commodity whose
  run log already holds an unblocked call reuses it without calling a model. Any other
  commodity, and the portfolio stage, copies its earlier log to
  `attempts/<key>.attempt<n>.jsonl` and starts the log over at the official key. So readers of
  `logs/` still see one attempt per run, and nothing logged is lost. A Step Functions retry of
  a crashed pipeline takes the same path. A slot already in the book is a no-op.
- **Point in time:** decisions stay `as_of` the scheduled time (the PIT reads, and the
  news search cached by the first attempt). News is filtered at date granularity, so a
  late retry can see a same-day article published after 06:00 that an on-time run
  wouldn't have. This is the accepted cost of the window. Fills are at the quote when the
  retry's portfolio stage runs, like any cycle.

**Credit warnings.** After every scheduled cycle, held or not, the portfolio Lambda reads the
DeepSeek balance (`/user/balance`) and Tavily credits (`/usage`), and alerts when either is
exhausted or covers fewer than `MIN_CYCLES_OF_CREDIT` (default 4) more cycles at this cycle's
rate (`notify/balances.py`). The rate is this cycle's LLM $ from its run logs, and for Tavily an
upper bound counted from the news agent's tool calls, on the 06:00 (searching) cycle only. A check that fails
is logged, never raised.

## Storage backend (v3)

- A small `Storage` protocol (`get`, `put_if_absent`, `list`, `put`) with
  `LocalStorage` (today's file layout, rooted at a directory) and
  `S3Storage` (same keys, one bucket). `pit_store`, `RunLog`, audits,
  `read_track_record`, the auditor scan, and the paper book all go through
  it — no direct `Path` I/O outside `LocalStorage`.
- **Immutability on S3:** `put_if_absent` uses S3 conditional writes
  (`If-None-Match: *`), so seven parallel Lambdas writing the same PIT
  release can't overwrite each other — the loser gets a no-op, matching
  `write_pit`'s existing "refuse to overwrite" semantics.
- **Bucket:** `ags-live-data-<account>`, from the platform `data-bucket`
  module: versioning on, encrypted, private, `prevent_destroy`, no lifecycle
  expiry; the function role has no `DeleteObject`. Keys:
  `data/…`, `logs/…`, `audits/…`, `portfolio/…`, `archive/pre-v3/…`.
- **Local mirror:** `scripts/sync_local.sh` = `aws s3 sync` **down only**
  into `data/`, `logs/`, `audits/`, `portfolio/`, `attempts/` (all, or only the prefixes named as arguments). Nothing syncs up. Local CLI
  runs use `LocalStorage` rooted at `local/` (also used for `--as-of`
  backtests), so they can never mix into the official record.

## Paper book (v3)

- **`Book` protocol, cTrader-shaped.** Models mirror cTrader's
  `ProtoOAPosition` / `ProtoOATradeData` / `ProtoOASymbol`: `position_id`,
  `symbol_id`/`symbol_name`, `trade_side` (BUY/SELL), `volume` in cTrader
  units (1/100 of a unit, as the API sends it), `price`,
  `open_timestamp`, `swap`, `commission`, `used_margin`, `money_digits`. Lots
  appear only at the edge: `lots = volume / 100 / lot_size`. `PaperBook` is
  the only implementation.
- **State = append-only cycle files.** Each cycle writes one immutable
  `portfolio/{book}/cycles/{cycle_id}.json` (`book` ∈ `manager`, `shadow`):
  opening state, stop-out replay result, swap accrued, inputs given to the
  sizer, raw and applied deltas, violations, fills, closing state, equity,
  peak equity. Current state = the latest cycle file. Replaying all cycle
  files from the start reproduces the equity curve exactly.
- **Order of operations per cycle:** (1) load last closing state; (2) replay
  H1 bars since the last cycle — accrue swap at each rollover crossed, check
  margin level against the stop-out level at each bar using the adverse
  extreme (low for longs, high for shorts), close the largest-loss position
  first as cTrader does; (3) mark to the current quote; (4) run sizer →
  guardrails; (5) fill at bid/ask; (6) write the cycle file.
- **Market data it needs** (all PIT, all through `read_pit`): symbol specs
  (`lotSize`, `minVolume`, `stepVolume`, `maxVolume`, `swapLong`,
  `swapShort`, swap calculation type, triple-swap day, margin rate), a
  bid/ask quote snapshot at fill time, H1 trendbars between cycles, daily
  bars for ATR. Specs are refetched daily and stored PIT, since swap rates
  change. H1 bars are captured for all seven symbols every cycle, not only
  the ones held: a counterfactual replay's book may hold what the official
  one didn't, and its stop-out replay needs those bars.
- **Drawdown-breaker reset:** an SSM parameter `/ags/<env>/breaker-reset-at`
  (a plain String, created by Terraform with value `none`), set by hand
  (`aws ssm put-parameter --overwrite`). The portfolio Lambda reads it and records
  the reset in the next cycle file — so the reset is logged and the book
  still has exactly one writer.
- **Demo account (`MANAGER_BOOK=demo`):** `DemoBook` wraps a `TradingGateway`
  (`CTraderDemoGateway`). The account is `CTRADER_TRADING_ACCOUNT_ID`, defaulting to
  `CTRADER_ACCOUNT_ID`; the shared token needs trading scope. The gateway checks scope and
  `isLive` and a hedging account type before any order. The account is shared with other
  strategies, so the manager's `balance` is a ledger carried in its own `closing_state`
  (starting capital `STARTING_EQUITY_USD` +/- its own deals), never the account's, kept in its
  own store `portfolio/demo/manager/cycles/` so the first demo cycle starts from scratch
  (`portfolio/manager/` stays the paper manager's, `portfolio/shadow/` the shadow's; see
  `portfolio/stores.py`); its
  positions are the ones labelled `ags`. Cycle records add `book_kind`, per-fill `status`,
  `commission`, `swap`, a `costs` summary, and `reconciled_closes` (closes the manager did not send).
  A demo account must also use the demo host for market data: set `CTRADER_DEMO=true`
  (`put_secrets.py` pushes it). The portfolio Lambda's timeout is 600 s, enough for seven
  re-quotes and up to 14 sequential orders at 30 s each. `scripts/demo_smoke.py` checks the
  connection; with `--trade` it opens and closes the minimum volume once.
- **No-live-execution test:** `tests/test_no_live_execution.py` greps
  `src/` (outside `demo_gateway.py`) for cTrader trading message names (`ProtoOANewOrderReq`,
  `ProtoOAAmendPositionSLTPReq`, `ProtoOAClosePositionReq`,
  `ProtoOACancelOrderReq`, `ProtoOAAmendOrderReq`) and fails on any hit.

## Guardrail engine (v3)

- `apply_guardrails(proposal, book_state, calls, specs, risk, config,
  breaker_active) -> (applied_deltas, violations)` — pure, no I/O, so every
  rule is a table-driven unit test.
- Rules run in the order of the design doc's table. Whole-proposal rejects
  (schema) short-circuit to "hold all." Per-leg clamps (direction, cap,
  churn, breaker, missing input) run before the portfolio-level scalings
  (grain bucket, gross, margin), and per-leg rounding to volume step runs
  last — rounding is always toward zero, so it can never push a leg back
  over a cap.
- Each violation: `{rule, symbol | null, proposed, applied, detail}`.
- The shadow sizer's output goes through the same function.

## cTrader credentials (v3)

cTrader Open API access tokens expire (~30 days), and a refresh spends the
old refresh token, so the AWS account holds **one** shared pair under
`/platform/shared/ctrader-*` with one writer: the platform's
`platform-ctrader-token-refresh` (daily, refreshes within 7 days of expiry,
alarms to `platform-alarms`). This project never refreshes and keeps no copy
of its own — only `/ags/<env>/ctrader-account-id` is per project, and it
has no SSM write permission (platform conventions § Secrets). Lambdas re-read
SSM per invocation, and once more on an auth error; if a stale
`/ags/<env>/ctrader-*` copy exists, the shared value wins. Local runs read
the shared pair with the developer's AWS credentials for any `CTRADER_*` key
`.env` leaves unset.

## Notifications (v3)

- One Telegram bot, one chat; `notify/telegram.py` is a thin `sendMessage`
  wrapper (HTML parse mode, long messages split).
- **Alerts:** CloudWatch alarms (Step Functions execution failed, Lambda
  errors) go to the shared `platform-alarms` SNS topic, whose notifier posts
  to Telegram — there is no ags-specific alarm forwarder. The notifier adds
  the cause on ALARM: the function's latest error lines in the window, or the
  failed execution's error and cause. `handlers/entry.py` logs one
  `ags-error handler=… commodity=… cycle_id=… run_id=… error=…` line before a
  handler's exception propagates, so that cause names the commodity or cycle.
  Direct alerts come from the portfolio Lambda (cycle held by a blocking
  provider error, credit running low, manager failed, commodity failed two
  cycles running, breaker tripped). A failed cTrader token refresh alarms
  from the platform.
- **Digest:** the `digest` Lambda is the last step of the cycle state machine, so a
  digest follows every cycle, and reads the latest two cycle files of each book and the run logs from S3. LLM
  spend is summed from the per-call `usage.cost_usd` that llmkit priced and
  the LLM layer records in run logs — the latest cycle's runs, portfolio log
  and their `attempts/` by agent and by commodity, plus the month-to-date
  total — with no second price table. Calls whose model llmkit has no price
  for are counted as unpriced, never guessed. No AWS spend (and no Cost
  Explorer access). Position values are volume × the cycle's marks from the
  record's `closing_state`, so nothing new is stored. Content and format:
  `DESIGN.md` § Notifications; the golden text is in `tests/notify/test_digest.py`.

## Infrastructure as code and deployment (v3)

Built on the `~/dev` platform (`platform/docs/deployment.md`); the shared
pieces are not repeated here.

- **Platform bootstrap** (applied once by hand from `~/dev/platform`) owns
  the state bucket `tfstate-<account>-<region>`, the GitHub OIDC provider,
  the `gh-deploy-ags` role (trusted only from this repo's `live` GitHub
  Environment, scoped to `ags-*` resources), the `ags` ECR repository, the
  `platform-alarms` topic + Telegram notifier, and the project's budget.
- **`infra/`** (state key `ags/live.tfstate`, S3-native locking): the
  `data-bucket` and `lambda-app` modules, pinned to a platform tag, plus the
  project-specific Step Functions state machine, its log group, the cycle
  schedule and its role, the SSM breaker parameter and the cycle-failed
  alarm (`cycle.tf`). The module creates the function role, log groups,
  functions, async retry config (0), the other schedules and an Errors alarm
  per function. Secret values never pass through Terraform.
- **Deploy** (GitHub Actions, platform reusable workflows):
  1. CI on every push: `uv sync --frozen` + `pytest` (unit only — the
     `addopts = "-m 'not integration'"` excludes integration tests, which
     hit real services and cost money).
  2. After CI succeeds on `main`, Deploy runs `lambda-deploy.yml` for `live`,
     which waits for the required reviewer on the `live` GitHub Environment.
  3. Build and push the image (tag = first 12 chars of the commit SHA),
     resolve it to a digest.
  4. `terraform plan` (shown in the job summary), then the `pre-apply` step
     `scripts/wait_for_cycle.sh ags-live-cycle` waits until no execution is
     `RUNNING` (15-min cap), so no cycle straddles two image versions.
  5. `terraform apply` of that plan.
- Schedules start disabled (`schedules_enabled = false` in
  `infra/env/live.tfvars`). Start one cycle by hand, check it, then flip the
  flag in a PR.
- Local CLI runs are the only sandbox; there is no dev/staging stack.

## Counterfactual replay

"What if the coordinator and manager had been a different model, prompt or
output format from date X?" A replay answers it without re-running anything
point-in-time-sensitive. Code: `src/ags/replay/`, run with
`scripts/run_replay.py`.

- **Spec** (`replays/{replay_id}/spec.json`): `fork_at`, and a `variant` +
  `model` for the coordinator and the manager. Variants are named prompt and
  output formats in `replay/variants.py` (`default` is the live agent); each
  must still return what the next stage reads. A different spec under an
  existing id is refused. DeepSeek models only for now.
- **Runs locally, not on AWS**, so a variant is just code in the working
  tree: a new prompt needs no deploy. `scripts/run_replay.py --spec <file>`
  reads the official record from the bucket and writes the replay back to
  it under `replays/{replay_id}/` (so a resume works from any machine). Per
  cycle, oldest first (each opens from the previous book): the seven
  coordinators in parallel, then the portfolio step. The replay never
  connects to cTrader. The books run under the deployed portfolio Lambda's
  limits (read from its environment), so they use the live guardrails.
- **What is re-run:** only the coordinator (per commodity) and the manager.
  Analysts are never re-run: their tools are PIT-bound, so the replay feeds
  the coordinator the official run's logged analyst outputs, and the
  official `price_at_call` (recomputed from stored daily bars if missing).
- **Track record:** official runs before the fork, the replay's own runs
  after it. Audits only for pre-fork runs (a post-fork audit grades the
  official call). Replayed calls are not audited: P&L is the comparison.
- **Books:** both replayed books (manager and shadow) start from the
  official *manager's* closing state at the last official cycle before the
  fork, so manager vs shadow compare from one starting point. Manual breaker
  resets are ignored: a replayed book that trips the breaker stays tripped.
- **Market data:** read-only, exactly what the official cycle captured —
  quotes at its `quoted_at`, specs and daily bars as of its `as_of`, H1 bars
  as of now bounded by its `as_of` (closed bars never change). The replay's
  market client refuses every call and its data store refuses every write,
  so missing data fails the replay instead of fetching or writing anything.
- **Which cycles:** official cycles from `fork_at` with a manager book file,
  so outage cycles (no book file) are skipped, as the official books skipped
  them. A re-run resumes: cycles already replayed are skipped, and a
  coordinator run already logged is returned without another model call.
  A coordinator or manager model call that *fails* (outage, billing) saves
  nothing as a decision: the coordinator logs an `error` event and no call,
  the manager writes no book file for the cycle (a live cycle would instead
  hold every leg). The run stops, and running it again asks the model
  again. A malformed reply
  is still a degraded call or a held proposal, as in live runs.
- **Writes only** `replays/{replay_id}/`: `logs/` (official log format),
  `portfolio/{manager,shadow}/cycles/`, `portfolio/logs/`.
- **Local copy** (`replays/{replay_id}/` in the repo, gitignored), written
  even when a run stops on a failure: everything under the replay's prefix
  in the bucket, plus
  - `report.csv`: per cycle, P&L since the fork of the official manager,
    official shadow, replayed manager and replayed shadow (each from its own
    book at the fork), and how many calls changed. Also printed at the end.
  - `differences.md`: the spec, the code version (commit, and whether the
    working tree had uncommitted changes), and for the coordinator and the
    manager the model and a diff of the system prompt (which carries the
    output format) between the latest replayed run and the official run at
    the same time — what was actually sent, so only real changes show.

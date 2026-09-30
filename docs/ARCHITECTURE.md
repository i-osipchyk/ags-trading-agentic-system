# Technical Architecture Decisions

Implementation-level decisions for the pipeline described in `CLAUDE.md` and
`Ags Trading Analyst — Pipeline Design v2.md`. Those two files define *what*
the system does and the guardrails it must respect; this file pins down
*how* it's built. Reached via a grilling session on 2026-09-30 — treat as
settled unless something concrete forces a revisit (not before the 2-month
review, per the design doc's Review checkpoint).

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
  logging/           # run_id generation, JSONL writer/reader
  ui/                # FastAPI app + Jinja2 templates, chat interface
run_pipeline.py       # CLI: trigger a weekly run
run_auditor.py         # CLI: manually trigger due checkpoint evaluations
tests/
  tools/               # PIT fixture-based unit tests
data/                   # PIT store contents (gitignored)
logs/                    # run JSONL logs (gitignored)
audits/                  # audit JSON files (gitignored)
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
- The log is the source of truth for both the auditor's frozen inputs and
  the chat interface's "no live tool calls" constraint.

## Trigger and UI

- **Pipeline trigger:** synchronous CLI, `python run_pipeline.py --commodity corn`.
  Generates `run_id`, `asyncio.gather`s the four analysts, passes results to
  the coordinator, writes the JSONL log, prints the final call. No web
  server, no queue, no background worker — a CLI is trivially replaced by a
  scheduled job later without redesign.
- **Chat interface:** a local FastAPI app (`uvicorn`, localhost-only) with
  server-rendered Jinja2 templates + minimal vanilla JS — not a JS
  framework/SPA, not Streamlit/Gradio. Scoped to a single frozen `run_id`;
  reads only that run's JSONL log and `read_thesis_log`/`read_track_record`
  equivalents; makes a fresh model call per question but with zero tool
  access, so "read-only, no live tool calls" is architecturally enforced,
  not just documented.

## Auditor

- **Trigger:** manual CLI, `python run_auditor.py`, no arguments. Scans all
  logged runs, computes which `(run_id, checkpoint_weeks)` pairs are due
  (`trigger_timestamp + {1,2,4,8} weeks <= now`) by diffing against what's
  already graded, and runs a separate evaluator invocation per outstanding
  pair.
- **Storage:** `audits/{run_id}/{weeks}w.json`, one file per graded
  checkpoint — separate from the pipeline's own `logs/`, so it can be
  matched back to the originating run and horizon independently.
- Idempotent by scan: a missed week self-heals the next time the command
  runs; nothing is lost, it just grades late.

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
  already-logged, already-tested data.
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
| cTrader Open API | `get_prices`, `get_curve` | Official Spotware Python SDK (Twisted-based), wrapped with `crochet` to expose an asyncio-facing interface; credentials via env vars the user provides |
| Tavily | News analyst search | LLM-shaped search API; free tier (1,000 credits/mo) comfortably covers worst-case usage (~140-280 credits/mo across the full commodity universe) |
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

## Secrets and config

- `.env` (gitignored) + `python-dotenv`, loaded through a single typed
  `config.py` (e.g. `Config.MAX_TOOL_CALLS`, `Config.DEEPSEEK_API_KEY`,
  `Config.CTRADER_CLIENT_ID`, etc.) — not scattered `os.environ[...]` calls.
- `.env.example` checked in with no real values.
- Fails fast at startup if a required key is missing, rather than failing
  mid-run partway through the four analysts.

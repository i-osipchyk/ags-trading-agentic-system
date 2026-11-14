# Roadmap — next steps after the first month

Written 2026-11-13 from a review of the first month of scheduled cycles (357 logged
runs, 2026-10-13 to 2026-11-12). It is a proposal, not a commitment: the order and the
open decisions at the end are the owner's to settle. `DESIGN.md` still governs what the
pipeline is; this file is about what to change next and how to tell whether it helped.

## 1. Where things stand

**Results are not yet evidence of skill.** Scored from the logged calls against daily
closes (entry at each call's intraday price, exit 1 or 5 trading days later; the 5-day
figures cover the 129 calls old enough to have an outcome):

| 1-week result | Hit rate | Mean move |
|---|---|---|
| Coordinator's directional calls (n=129) | 75% | +1.37% |
| "Always short" on the same dates | 87% | +2.83% |

All seven commodities fell over the period (0.2% to 5% a week), and 65% of the calls were
bearish. The 33 bullish calls hit 24%. Mean 1-week P&L by the ISO week the call was made
was -0.75%, +0.18%, +2.25% and +3.7%, so the gain is concentrated in the last two weeks of
a hard sell-off. The effective sample is about 27 commodity-weeks. The paper manager ended
at $10,303 against the shadow's $10,420, which is noise at this size. A shadow replay on a
flat $100k account (all seven commodities tradable, fills priced from M5 bars) returned
+4.0% against +4.2% for the official 10k shadow.

**What the coordinator actually relies on.** It stays within its inputs: 93% of the numbers
in its 160 most recent theses appear in the analysts' outputs, 6% only in its track-record
tool, 1% in neither (mostly derived values). Its reliance is uneven, though:

| Signal (analyst's implied direction) | Standardized weight on the call |
|---|---|
| News sentiment | +0.39 |
| Supply/demand trend | +0.11 |
| Weather risk | +0.02 |
| Technical trend labels | +0.01 |

(OLS on 322 runs, R² 0.29; read as relative size, not significance. The technical proxy sees
only the three trend labels, not the chart narrative.) When technical and news disagree the
call follows news 53% of the time, technical 13%, and goes neutral 34%. Stated reliance
differs: technical is named supporting in 86% of runs and is 42% of `key_drivers`. News is
the only analyst that gives a verdict per item; technical, weather and supply/demand give
evidence only.

**Defects found and fixed during the review** (merged, deployment to be verified):

| Defect | Effect | Fix |
|---|---|---|
| Quotes stored under the wrong symbol | Bad marks and fills in some cycles | PR #13 |
| WASDE and coffee cached forever | Corn used the Oct 9 WASDE until Nov 10; coffee a Jun 2025 report until Nov 11 | PR #14 |
| Weather cache never refreshed | Drought map frozen at Oct 6 and CPC outlook at Sep 30 in every cycle Oct 13 to Nov 10 | PR #15 |
| Supply/demand compared different marketing years | 28 of 34 runs with a prior showed a meaningless revision; corn said "tightening" in 44 of 51 runs while October's WASDE loosened | PR #15, PR #17 |
| Weather regions for coffee, sugar, cocoa fell back to US states | 29, 30 and 26 runs assessed Georgia and Texas; weather dissented in 15 of 30 sugar runs | region lists, Nov 2 |
| Cocoa had no supply/demand source | Degraded in every run until Nov 2 | grind releases, Nov 2 |
| News dates written by the model | A Nov 9 article restated as Nov 10 | PR #15 |
| Technical: volatility labelled "trailing_average", seasonality from 3 numbers, model-written key levels | Noisy or unverifiable inputs | PR #16 |
| Supply/demand was an LLM copying tool output | Free-text labels, no change within a month | deterministic, PR #17 |

**Still open:** news is date-only and a median 13 days old (14% within one day), searched
once a day and carried forward at 12:30 (45% of runs); only news gives a verdict; conviction
is 3 on about 80% of calls and says little; the schedule changed four times in the first
weeks (the early 20:00 UTC cycles ran with markets closed); and the 2026-11-10 WASDE moved
corn +2.5%, soybeans +3.4% and wheat +0.9% in the 15 minutes before the 12:30 ET cycle, which
cost the book about 1.7%.

**Tavily cannot reproduce a past search.** Four queries from the 2026-10-29 10:00 UTC cycle
were re-sent on 2026-11-13 with the same date bound: only 2, 4, 1 and 5 of 10 URLs matched,
some shared articles had a different date or snippet, and with the window narrowed to the
days around the original results only 3 of 7 and 2 of 6 of the recent articles came back.
The stored results from live cycles (the PIT cache) are the only faithful record of what a
cycle saw.

## 2. Principles for the next phase

- **One change at a time, and a challenger before a replacement.** A new prompt or source
  runs next to the live one (shadow, outside the official record) and is compared on the
  same cycles. Changing prompts and adding grounding together would hide which one helped.
- **Only the scheduled AWS cycle writes the official record** (`CLAUDE.md`). Challengers and
  replays write to their own namespace, as the counterfactual replay already does.
- **Backtest only what can be reproduced.** Prices, WASDE, COT and observed weather can be.
  News and forecast weather cannot, and a model may remember how a past period ended
  (`CLAUDE.md`, "Backtest leakage"). Forward testing is the clean read.
- **Every run says which code and prompts produced it,** so a scorecard can be cut by version.

## 3. Steps

### Step 0 — housekeeping before any experiment

1. **Freeze the setup and restart the review window from a clean date.** The first month mixes
   the defects above. Choose the date when all of them are deployed and verified.
2. **Stamp each cycle** with a code version and a hash of the prompts in the run log.
   *Done:* the `pipeline`/`version` event (`ARCHITECTURE.md` § Logging and replay).
3. **Alarm on stale data.** Log each source's latest release date and age; flag when it is
   older than expected (drought map over about 10 days, WASDE over about 40, news older than a
   few days). This would have caught four of the defects above within a day.
   *Done:* the `pipeline`/`freshness` event plus one daily alert. The 40-day CPC and 120-day cocoa
   limits are my choices, not from this review; adjust in `freshness.py`.
4. **Verify each deploy** with the first live cycle: the drought map date, WASDE date and
   outlook issue date in the logs, and no model call for supply/demand.
5. **Build the scorecard before the experiments.** Sync the audit files from S3 (they exist
   there; the local mirror only had 7 dev ones) and report effective counts with these
   baselines: always-short, always-long, a mechanical trend rule, and a market-neutral view
   that removes the market's drift. Set the pass bar in advance (see Open decisions).
   *Built:* `ags/scoring.py`, the UI's Scorecard page and `scripts/run_scorecard.py` (`ARCHITECTURE.md`
   § Trigger and UI). It scores from the logged calls and closes, not the audit files. The pass bar is
   the proposal below, set in code but **not yet confirmed by the owner**. The numbers differ from § 1
   (weighted by effective count, a market-neutral view, and the exit counted from the call day's own
   close), so § 1's table is not reproduced by it.
6. **Store the raw Tavily response** next to the filtered one, with the fetch time.
   *Done:* `tavily/search_raw/` (`ARCHITECTURE.md` § External data sources).

### Step 1 — align the analysts

Give every analyst the same output: `bias` (-2 to +2), `confidence`, a stated horizon,
drivers and invalidation conditions, alongside today's evidence fields. Notes:

- Analysts stay blind to each other and to the coordinator.
- Horizons really differ (WASDE is monthly, technical about a week), so each states its own
  rather than being forced to one week.
- Supply/demand and the technical trend states are code, so their bias should be computed in
  code (the stocks-to-use revision; the trend states), with the model only commenting.
- The point is per-analyst scoring and a coordinator that weighs verdicts instead of picking
  the one analyst that happens to give one.

### Step 2 — evaluate it

1. **Replay on the live archive.** Re-run analysts and coordinator against the stored caches
   with fetching disabled, old prompts against new. No leakage and exact inputs, but about five
   weeks of one falling market, so it tests mechanics (do biases line up with the call, what
   does each ablation change), not skill. The counterfactual replay (`scripts/run_replay.py`)
   currently re-runs only the coordinator and manager; this extends it to analysts.
2. **Forward champion and challenger.** Run the aligned-prompt version in shadow from a fixed
   date for several weeks and compare on identical cycles. Model cost is small (about $5 for
   the first month), so doubling it is cheap.
3. **A multi-year backtest only for the reproducible subset** (prices, WASDE, COT, observed
   weather), with commodity names and dates masked or a model whose training cutoff predates the
   period. News and forecast weather stay out of it.

### Step 3 — Google grounding as another challenger

For news first (the input the coordinator relies on most and the one with the worst age
problem), then supply/demand estimates, then weather (least value in harvest season). Free
text adds what the reports lack, for example trade estimates against the USDA number on a
report day. Requirements:

- **Archive the grounded responses live**: the answer text, its sources and titles, and the
  retrieval time. They cannot be fetched again later, so this is forward-only; start capturing
  early, even before the coordinator uses them.
- **Require a source URL and a date per claim** and check them in code, like `ground_headlines`
  does for news, since the model has restated dates before.
- **Keep the supply/demand lane narrow** (estimates, export sales, crop condition reports), so
  it does not duplicate news. The deterministic report figures stay as the facts.
- Run it as its own challenger, separate from Step 1.

### Other candidates

- **Report-day handling.** Trim grain exposure before 12:00 ET on WASDE and similar dates and
  refresh inputs at 12:30. Over 21 reports the move to the close averaged 1.27% for corn
  against 0.26% on ordinary days, and a trend-aligned grain basket averaged +0.30% with a
  worst of -1.66%, so it is a risk-preference choice, not a clear edge.
- **Conviction.** Replace the near-constant integer with a probability or score, and test the
  coordinator with and without its track-record input (about 310 KB per run, which may anchor
  it to its own past calls).
- **Model bake-off on the replay harness.**
- **More inputs, each as a challenger:** USDA export sales and crop progress, ICE certified
  stocks, USD/BRL, crude, the soy-corn ratio. Keep one only if it moves per-analyst scores.
- **Weather detail:** an area share instead of raw square miles, and a normal to compare
  non-US forecasts against. The Drought Monitor date-only handling leaks up to two days in a
  backtest (live cycles are unaffected).

## 4. Order

| Step | What |
|---|---|
| 0 (first) | Freeze and stamp, freshness alarms, deploy checks, audits synced, baseline scorecard |
| 1 | Aligned-prompt schema with per-analyst scoring |
| 2 | Replay on the live archive, then a forward shadow challenger |
| 3 | Grounding for news, then supply/demand estimates, then weather |

## 5. Open decisions

- The clean start date for the review window.
- The pass bar. For example: beat the mechanical trend rule market-neutral over at least 8
  effective weeks, including a rising or choppy stretch.
- The shape and scale of `bias` and `confidence`, and each analyst's horizon.
- Whether to trim exposure around report days.
- Which grounded source to use and what to pay; neither is settled.

## 6. Unverified

- Whether Gemini grounding with Google Search has a date filter, and its pricing. As far as
  the review could tell it has no date filter; check before relying on that.
- How far back Tavily's index goes for agricultural press, and what happened to the
  articles that stopped coming back (removed, or no longer matched).
- Whether the deployed Lambda has picked up PRs #14 to #17 for every source. The logs showed
  WASDE and coffee updated from 2026-11-11; the weather fix merged after the 2026-11-12 17:30
  UTC cycle, so its first check is the next cycle.

## 7. How the figures were measured

From the run logs (`logs/`), the portfolio records (`portfolio/`) and the PIT caches
(`data/`) as of 2026-11-13. Forward returns use cTrader daily bars; the shadow replay on 100k
used M5 bars fetched from cTrader for fills (`scripts/run_shadow_replay.py`). The coordinator
weights come from an ordinary least squares fit of the call (+1 bullish, 0 neutral, -1
bearish) on each analyst's implied direction, derived from structured fields: the mean of
the three trend states, the net share of bullish headlines, the stocks-to-use trend, and the
average weather risk (severe 2, watch 1, benign 0). The Tavily comparison re-sent six
searches on 2026-11-13. The figures will change as more cycles accumulate.

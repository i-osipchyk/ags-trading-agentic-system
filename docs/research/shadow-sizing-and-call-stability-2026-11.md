# Shadow sizing, call stability and a trend baseline: first backtests, November 2026

Run 2026-11-17 on the official record pulled with `scripts/sync_local.sh` (48 cycles, 2026-10-14 to 2026-11-17, seven commodities). Everything here is a **local, read-only backtest** on logged calls: nothing was written to the official record or the bucket, and no model was called. Replays are in `replays/` (git-ignored, local). The only network use was cTrader M5 market data, cached under `replays/*/m5/`.

## Summary

- **The shadow's sizing is not what drives results.** Conviction is almost always 3 (DeepSeek rarely uses 1 or 5), so conviction-based sizing is close to one fixed size. Dropping conviction (flat 1% ATR budget) changes little.
- **The official $10k shadow is a different portfolio from the same rule at $100k+.** Contracts trade in whole lots with a 1-lot minimum and guardrails round toward zero, so at $10k the softs (coffee, cotton, cocoa) never reach 1 lot and are never traded. The $10k replay matches the official shadow (+2.30% against +2.31%), so fill pricing is not the cause.
- **The calls flip far more than the trend they roughly follow.** Calls change side on 28% of cycles per commodity (89 of 315); the medium-term trend state changes on 3% (11 of 315). The post-WASDE week lost mainly to price moves while the book was whipsawed, plus spread on about 36 fills.
- **A confirmation rule on flips (N consecutive opposite calls) removed most of the damage in this window.** At $1M conviction sizing: −1.41% as is, +2.54% with N=2, +2.73% with N=3. N=3 is no clearly better than N=2.
- **A simple trend rule did at least as well as the strategy after costs, but only for one horizon.** Short-term trend: +2.45% at $1M; medium-term trend: −8.92%. Neither was chosen in advance and the window has one big reversal, so this is a hint, not a conclusion.
- **Nothing here is statistically settled.** 48 cycles, five weeks, consecutive calls overlap, one regime (every commodity fell 2–13%).

## How it was run

- **Script.** `scripts/run_shadow_replay.py` replays the shadow book over the logged calls from a flat account. Calls, specs and daily bars come from the local mirror; fills are priced from M5 bars (the bid is the open of the M5 bar containing the instant, ask = bid + the symbol's median stored spread). The manager stage is stubbed to hold everything. Resumable; a rerun with the same id reuses finished cycles.
- **Options added this session** (backtest-only patches inside the script; nothing in `src/` changed):
  - `--sizer conviction|flat_hold|flat_exit|risk_hold|risk_exit`. `conviction` is the deployed rule. `flat_*` is one lot long or short on a bullish or bearish call; `risk_*` is the full risk budget (1 ATR = `SHADOW_RISK_BUDGET` of equity, the deployed rule at conviction 5). `*_hold`: a neutral call does nothing (the deployed guardrail freeze). `*_exit`: a neutral call closes the position. Guardrail 2 freezes a neutral leg, so `*_exit` patches `apply_guardrails` in the process (a neutral call is passed as bullish when the target is flat).
  - `--trend short|medium`: replaces every call with the technical analyst's logged trend state of that horizon (up = bullish, down = bearish, else neutral; conviction 5).
  - `--confirm N`: a commodity's position direction flips only after N consecutive opposite calls. The first directional call opens, a neutral or failed call resets the count, and an unconfirmed opposite call is passed on as neutral.
  - M5 cache top-up: the cache is extended from its last bar instead of failing when new cycles arrive.
- **Equity and limits.** The deployed portfolio Lambda's limits are read from AWS, and the defaults are used if AWS is unreachable. Starting equity is set per run (`--equity`); the figures use $10k, $100k and $1M.
- **Per-commodity P&L** is computed outside the book as `closing_lots(t) × lot_size × price factor × (bid(t+1) − bid(t))` at M5 opens, with price factors 100 (corn), 1 (soybeans, wheat), 100 (sugar, cotton), 10 (coffee), 1 (cocoa). It ignores spread and swap, so it differs from book equity by those costs. For the 100k conviction replay the week after WASDE it gave −$3,650, against −$3,660 from the book.
- **Cost split** (price, swap, spread, commission) for the 100k replay is from the cycle records: spread from `costs.spread` (fill price against quote midpoint), swap from realized swap shares on closing fills plus the change in open-position swap, price as the remainder. Commission is zero in the paper book.

## Results

All figures are equity change since start. "As is" is the deployed behaviour (N=1).

### Official shadow against replays of the same rule

| Conviction sizer | Final |
|---|---|
| Official shadow, $10k | +2.31% |
| Replay, $10k | +2.30% |
| Replay, $100k | −0.55% |
| Replay, $1M | −1.41% |

The account size changes the result because of lot granularity. At $10k, 1% ATR is $100 of risk and the softs are too big for it: they are clamped to flat (minimum volume) and the grains round down to 2–4 lots. Rough per-commodity price P&L (percent of start, M5-based):

| | $10k | $100k | $1M |
|---|---|---|---|
| Corn | +0.97 | +1.24 | +1.03 |
| Soybeans | +3.01 | +2.36 | +2.76 |
| Wheat | +1.33 | +1.61 | +1.52 |
| Sugar | −2.32 | −2.32 | −2.43 |
| Coffee | 0 | −0.40 | −0.75 |
| Cotton | 0 | −2.02 | −3.06 |
| Cocoa | 0 | +0.52 | +1.50 |

Spread and swap also grow with the number of positions, so the residual between this and book equity rises with account size (about −0.7 points at $10k, −2 at $1M).

### Sizing rules

| Rule | $10k | $100k | $1M |
|---|---|---|---|
| Conviction (deployed) | +2.30% | −0.55% | −1.41% |
| 1 lot, neutral = hold | −6.21% | −0.62% | |
| 1 lot, neutral = close | −2.24% | −0.22% | |
| 1% ATR, neutral = hold | +1.69% | −2.22% | −2.48% |
| 1% ATR, neutral = close | −1.50% | −1.91% | −2.01% |

- One lot is a very different risk at different sizes (about 1% of equity at $10k, 0.1% per ATR at $100k), so the one-lot rows are not comparable across columns.
- "Neutral = close" beat "neutral = hold" at $100k and $1M but not at $10k for the 1% ATR rule. It is **not** a consistent improvement.
- All rules give back most of their gain after about 2026-11-10; no sizing rule avoids it.

### The WASDE day and the week after

Official shadow, $10k. 2026-11-10 12:30 ET cycle: about −$180 (corn −78, soybeans −92, wheat −24), the book being short corn, soybeans and wheat into the jump. The week after: −$189 (equity $10,420 to $10,231). Price P&L −$135: sugar −$118, wheat −$28, corn and soybeans about flat; the other roughly −$55 is spread and swap on 20 fills. After the spike the book reversed to long corn and soybeans (buying after the move), then flipped sugar four times in six days (−$44, −$29, −$17 realized on the closes).

100k conviction replay. WASDE cycle: −$2,115 (106,116 to 104,001). Week after: −$4,548.

| Commodity | Price P&L, week after |
|---|---|
| Sugar | −$1,535 |
| Cotton | −$1,090 |
| Coffee | −$511 |
| Cocoa | −$385 |
| Wheat | −$251 |
| Corn + soybeans | about +$120 |

Cost split, 100k conviction replay:

| Window | Total | Price | Swap | Spread | Commission |
|---|---|---|---|---|---|
| Whole backtest | −$547 | +$722 | +$202 | −$1,471 | $0 |
| To the 11-10 peak | +$6,116 | +$6,496 | +$326 | −$706 | $0 |
| WASDE cycle | −$2,115 | −$2,115 | 0 | 0 | $0 |
| Week after | −$4,548 | −$3,660 | −$124 | −$765 | $0 |

Over the whole period the calls made a small price gain that costs more than ate.

### Why some commodities gain and others lose

1M conviction replay, price P&L only, with the hit rate of the directional calls against the next cycle (about 12 hours) and the next week:

| | Price P&L | Next cycle | +1 week | Market move |
|---|---|---|---|---|
| Soybeans | +$27.6k | 58% | 69% | −5.4% |
| Cocoa | +$15.0k | 51% | 74% | −11.6% |
| Wheat | +$15.2k | 52% | 61% | −9.2% |
| Corn | +$10.3k | 58% | 72% | −5.6% |
| Coffee | −$7.5k | 32% | 65% | −2.8% |
| Sugar | −$24.3k | 28% | 53% | −12.7% |
| Cotton | −$30.6k | 48% | 26% | −2.0% |

- Every commodity fell, and the calls were mostly bearish (for example wheat 36 of 46), so the grain and cocoa gains are largely a short book in a falling market.
- Sugar lost although it fell 12.7%: its calls flipped constantly. Its data is as complete as the grains' (3 of 57 supply/demand outputs degraded). This is call instability, not data.
- Cotton is the only commodity wrong at the weekly horizon (5 of 19). Its supply/demand analyst was bullish on paper all period and the market ignored it.
- Cocoa has the worst inputs (supply/demand degraded in 33 of 57 runs, no figures in 28; USDA has no cocoa report) and still made money. Data availability does not predict the P&L here.
- Coffee, cocoa and cotton have forecast rainfall and temperature but no drought index. The freshness check flagged the drought map and the CPC outlook as stale in the one run inspected (41 and 47 days old against 10 and 40 allowed); how often is not measured.
- At about 25–40 directional calls per commodity the hit rates carry a margin of roughly ±15–20 points.

### Is it trend following?

- Directional calls match the technical analyst's short-term trend 70% of the time (180 of 256) and the medium-term trend 53% (136 of 256).
- Calls flip 28% of cycles per commodity; the medium-term trend state flips 3%. The strategy has a trend follower's exposure to sideways markets with about ten times the turnover.

Trend-only baselines (conviction 5, same costs, guardrails and sizing), $1M:

| Rule | Final | Price P&L | Spread | Fills | Reversals |
|---|---|---|---|---|---|
| Calls, deployed conviction sizing | −1.41% | +$5.7k | −$18.3k | 112 | 32 |
| Calls, full 1% ATR (like for like) | −2.48% | +$6.7k | −$28.7k | 220 | 32 |
| Short-term trend rule | +2.45% | +$23.8k | −$6.4k | 159 | 3 |
| Medium-term trend rule | −8.92% | −$76.9k | −$6.1k | 147 | 3 |

The medium-term rule lagged and stayed short through the WASDE bounce. Which horizon is "the" baseline matters more than the strategy-versus-baseline gap.

### Confirmation before flipping

| Setting | As is | N=2 | N=3 |
|---|---|---|---|
| $1M, conviction | −1.41% | +2.54% | +2.73% |
| $1M, 1% ATR budget | −2.48% | +3.08% | +3.53% |
| $100k, conviction | −0.55% | +2.90% | +2.89% |
| $10k, conviction | +2.30% | +3.33% | +4.32% |

- $1M conviction spread paid: $18.3k, $9.2k, $8.7k; fills 112, 73, 67; reversals 32, 13, 11. Worst point −1.4% (as is) against −0.4% (confirmed); peaks +5.1%, +7.9% (N=2), +7.1% (N=3).
- The effect is a step from N=1 to N=2; N=3 adds little and enters a cycle later. N=2 is the sensible default. The gain is larger than the saved spread (about 4 points against 0.9 at $1M), so most of it is avoided losing reversals.
- The $10k N=2 against N=3 gap is whole-lot noise (a single position moves it by about a point).
- **Cost of the rule not visible in this window:** in a sharp, lasting reversal the position enters one cycle late.

## Caveats

- Five weeks, 48 cycles, one regime, one large reversal (WASDE). Hit rates and all gaps between rules are within noise. Consecutive calls overlap and are not independent samples.
- Replay fills are priced from M5 bars with a median spread, not the broker's quotes. 87 of 546 decision and execution quotes fall in a closed market and use the last bar close.
- The shadow has no commission model, so costs are spread (plus swap) only; the Pepperstone demo book would add commission.
- Per-commodity P&L is an approximation (M5 opens, ignores spread and swap); it agrees with the book to within about $10 for the week analysed and by about 1–2 points over the whole period.
- N=2 was run once, as requested, and N=3 once; there is no parameter sweep, but also no out-of-sample period.
- The trend baselines use the technical analyst's logged trend states (point-in-time) with fixed conviction 5, so they trade full size every time, unlike the deployed conviction sizing.

## Testing plan

Ordered by value. Each item is a replay or a script run on the existing record unless marked otherwise.

1. **Fix the shadow baseline's account size.** Decide whether the shadow should start at a size where the softs can trade (for example $100k) or accept that it trades grains and sugar only. Any sizing comparison at $10k is dominated by rounding. Re-run the comparison at the chosen size.
2. **Trend-continuation coordinator (counterfactual replay).** Add a coordinator variant to `src/ags/replay/variants.py`, with the logged trend (short and medium both) plus news, supply/demand and weather as input, and an output of trend continues, stalls (neutral) or reverses, mapped to the existing call shape. Run it with `scripts/run_replay.py` over the 48 logged cycles (about 336 coordinator calls per run), three times to see the spread. Compare against the current coordinator, the short- and medium-trend rules, and the confirm-2 version, at $100k and $1M. Check how often the model just agrees with the trend. The replay spends LLM credits and writes under `replays/{replay_id}/` in the bucket (not the official record), so it needs explicit go-ahead.
3. **Why the calls flip.** Join each flip with the analyst outputs of the two cycles around it. If the inputs changed (news, trend break), a filter is reasonable. If they didn't, the instability is model noise and the fix is the prompt or the model (conviction clustering at 3 is part of the same effect).
4. **Confirmation rule as a deployable shadow rule.** If kept, implement confirm-2 on the shadow only: confirm-before-flip, neutral exit handling (an `allow_neutral_exit` flag on `apply_guardrails` for the shadow), a design-doc update (`DESIGN.md` sizing formula, `CLAUDE.md`), tests, and keep the current shadow running beside it so the track record stays comparable. Decide separately whether the manager gets it too.
5. **Per-analyst, per-commodity scorecard.** Run the auditor's scorecard (`scripts/run_scorecard.py`) to test the data-availability theory with graded analyst drivers, not call hit rates. Measure how often the drought map and CPC outlook are stale across all runs.
6. **Wait for more data.** Repeat the whole comparison at about 3 months (the planned review) and across more than one regime. Weight overlapping calls (see `DESIGN.md`) when scoring.
7. **Costs.** Add a commission model to the shadow, or compare against the demo manager's real costs, so the manager-versus-shadow comparison and any rule change are measured net of costs on both sides.

## Files

- Script: `scripts/run_shadow_replay.py` (modified, uncommitted).
- Charts: `replays/sizer_comparison.png`, `replays/sizer_comparison_risk.png`, `replays/sizer_comparison_1m.png`, `replays/sizer_comparison_10k_100k.png`, `replays/strategy_vs_trend_1m.png`, `replays/confirm2.png`, `replays/confirm_n123_10k_100k.png`.
- Replays: `replays/shadow-100k*`, `replays/shadow-10000*`, `replays/shadow-10k-*`, `replays/shadow-1000000*`, including `-confirm2-*`, `-confirm3-*` and `-trend_*` variants.

# Google grounding against Tavily for news: first comparison, November 2026

Run 2026-11-15. Model `gemini-3.8-flash` through the Gemini Developer API (AI Studio key, billing enabled), tool `google_search`. Nothing here touches the official record: the client is not wired into the pipeline, and every grounded answer was written to `grounding_compare/` (git-ignored, outside `data/`). That directory is the only copy of the raw answers, because Google cannot replay a past search; the figures below were recomputed from it. Price is covered in [google-grounding-pricing-2026-11.md](google-grounding-pricing-2026-11.md).

## Summary

- **Live (as_of now), grounding gives fresher news from fewer items.** On the 35 queries of the 2026-11-13 cycle, run on both providers at 2026-11-15 03:35 ET: items from the last 3 days 78 against 76, from the last 7 days 139 against 127, older than 30 days 11 against 150. Median of per-query median ages: 4 days against 30.
- **The two providers barely agree.** 17 shared URLs on the same query; 276 unique Tavily URLs, 146 grounded, 17 in both. Their domains differ too. They look complementary, not interchangeable.
- **The `time_range_filter` is not enforced.** A replay as of 2026-11-09 06:00 ET (run on 11-15) came back 86% dated after the cycle. A backtest or replay cannot use grounding; only forward capture can.
- **Accuracy held up in the sample.** Headlines matched page titles on every loaded page checked, 27 of 27 verifiable dates were within a day of the page's own date, and 24 of 26 figures in snippets appear on the page (the other two are fractions and ranges my matcher can't parse). Tavily's dates were wrong on 4 of 27 verifiable items.
- **Cost looks small** (about $0.87 a week if the free search allowance is ours, $5.84 if not), with the allowance's scope the main unknown. See the pricing note.

## How it was run

- **Client.** `src/ags/tools/sources/grounded_news.py`, `GroundedNewsClient`: the `NewsClient.search(query, *, published_before)` seam, so `search_news` and its point-in-time (PIT) filter work with it unchanged. The model is asked for one `DATE | HEADLINE | SNIPPET` line per article; each line is taken from a grounding support's segment text, and its URL from the grounding chunk the line is cited to. Lines with no valid ISO date or no cited source are dropped. Chunk URLs are Google redirects, resolved to the publisher by reading the redirect's `Location` header. The request carries a 7-day `time_range_filter` ending (exclusive) at midnight UTC on `published_before`. Auth failures (401, 403) and a 429 whose message says "billing details" are recorded as blocking in `ags.blockers`, like Tavily's.
- **Comparison.** `scripts/compare_grounding.py` with `src/ags/tools/grounding_compare.py` (`compare`, `pit_filter`, `fetch_cached`). It reads the Tavily searches the cycles already archived in `data/tavily/search_news`, asks grounding for the same query, and caches each billed answer by (as_of, commodity, query), so a rerun costs nothing. It is a dry run unless `--run` is given, and `--run` needs `--limit N` or `--all`. `--live-as-of now` re-runs the queries on both providers with as_of set to the run time.
- **Filters.** Both sides are judged by the same rule `search_news` applies: an item with no date, or dated on a day not yet released at `as_of`, is dropped (end-of-day release in ET for a source not listed in `RELEASE_TIMES`).
- **Not compared.** Tavily's stored results were taken as archived (filtered upstream). Tavily's per-call credit use is not in any figure.

## Results

### Live: 35 queries, both providers, as_of 2026-11-15 03:35 ET (a Sunday)

| | Tavily | Grounding |
|---|---|---|
| Items after the PIT filter | 313 (6 dropped) | 164 (0 leaked) |
| Dated within 3 days | 76 | 78 |
| Dated within 7 days | 127 | 139 |
| Older than 30 days | 150 | 11 |
| Median of per-query median ages | 30 days | 4 days |
| Queries with nothing from the last 7 days | 6 | 2 |

Top domains, Tavily: farmprogress 29, agrolatam 23, tradingview 19, commodity-board 16, barchart 14, brownfieldagnews 12. Grounding: barchart 30, commodity-board 17, tradingview 14, brownfieldagnews 9, ukragroconsult 6, cropgpt.ai 5.

Usage for the grounded side: 71 Google searches (2.03 per request), prompt 8,309 tokens, output 10,268, thoughts 34,647, tool use 0.

The snapshot is one Sunday morning, so "the last 3 days" is mostly Thursday and Friday. Grounded answers differ from run to run for the same query, so one run per query is not a stable estimate.

### Replay: the 2026-11-09 06:00 ET cycle, run on 2026-11-15

| | Tavily (archived) | Grounding |
|---|---|---|
| Items | 316 kept | 154 returned, 133 dated after as_of, 21 kept |
| Queries with no surviving claim | | 23 of 35 |
| Shared URLs | | 1 |
| Queries sharing a domain | | 3 |

The 154 claims are dated mostly 2026-11-10 to 11-14 (26, 24, 22, 37 and 12 on those days) with 12 on 11-09. The SDK does send `timeRangeFilter` (end 2026-11-09T00:00:00Z, checked by serialising the tool); Google ignores it for this model and these queries. Usage: 74 Google searches, 8,411 prompt, 9,952 output, 36,021 thought tokens.

This is a property of replaying: Google searches today's web, so the future exists. In a live cycle as_of is now and nothing is dated after it, as the live run shows (0% leaked). The live pipeline would still depend on model-asserted dates.

### Quality: 42 items per provider, checked against the pages

Stratified sample (6 per commodity per provider) of the live results, fetched with plain HTTP and parsed in memory.

| Check | Grounding | Tavily |
|---|---|---|
| URL loads | 33 of 42 | 33 of 42 |
| Article-shaped URL | 40 of 42 | 42 of 42 |
| Headline matches page title (similarity 0.6 or more) | 31 of 31 with a title | 32 of 32 with a title |
| Claimed date within 1 day of the page's date | 27 of 27 | 23 of 27 |

- **Tavily date errors.** Four of 27 are off by 3, 16, 18 and 29 days; three are claimed as 2026-10-16 against real dates of 09-17, 09-28 and 09-30. The claimed date is later than the real one, so these look fresher than they are, the safe direction for PIT. A claimed date earlier than the real one would be a leak; none was seen.
- **Grounding's figures.** Of 15 sampled snippets containing numbers, 24 of 26 figures appear on the page; the two misses are "4 to 7 cents" and "9 3/4 cents", formats the matcher cannot parse.
- **Non-article results from grounding.** One sampled result was a YouTube video and two were quote or index pages (about 7% of the sample). A URL filter would remove them.

Limits of the check: 9 of 42 pages per provider returned 403 or 202 (bot protection) and are unverified; 6 loaded pages per provider exposed no publish date; snippet fidelity beyond figures was not read; Tavily snippets were not tested. The scripts were throwaway and are not in the repo.

## What changes in the roadmap

- Grounding has a time filter in the API, but it is not enforced, so Step 3 can only be evaluated forward, as already planned. Archive each live answer (the cache layout above is a starting point).
- The `usage` block captured in `last_raw_response` (token counts and number of searches) is the input to cost per query.
- Because the providers overlap so little, the useful challenger may be both together in one cycle, not a replacement.

## Open items

- Spot-check more than 42 items per side, with the 9 blocked pages per side handled another way.
- Whether a 429 quota message and a per-minute rate-limit message are worded differently. Only the quota wording was seen live, and the blocking rule matches on it.
- Whether the model's asserted dates drift on days with less news than a Sunday morning.
- A URL-shape filter for videos and quote pages.

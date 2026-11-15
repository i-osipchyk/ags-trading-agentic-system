# Grounding with Google Search on Gemini 3.8 Flash: cost, November 2026

Researched 2026-11-15. Read-only: no Gemini or Tavily call made, `.env` not read, no code changed. Evidence comes from Google's own documentation, fetched during this session. Anything I could not confirm on a primary page is marked **UNVERIFIED**.

Note on URLs: the `ai.google.dev/gemini-api/docs/...` pages now answer `301 Moved Permanently` to `aistudio.google.com/docs/...`. The links below are the redirect targets I actually read. None of the pages shows a "last updated" date, except the model page ("September 2026"). The pricing page gives a price-change date (2027-01-01) instead.

## Summary

- Per-token: $0.75 per 1M input and $3.75 per 1M output (thinking tokens included in output) through 2026-12-31. Both double on 2027-01-01.
- Grounding: $14 per 1,000 search requests, after 5,000 free per month, paid tier only. On Gemini 3 and newer the unit is each search query the model executes, not the prompt.
- One week of our news search (about 175 requests, about 355 searches): about **$0.87 in tokens**, and a **grounding fee of $0** while the monthly free allowance holds (list price would be $4.97). Expected total about **$0.87 a week**; range **$0.22 to $8.22** across the assumptions below.
- Most uncertain input: whether the 5,000 free searches a month are really ours alone (the page says shared across all Gemini 3 and newer models, scope not stated per project).

## Price table

| Item | Value | Source |
|---|---|---|
| Input, Standard, paid | $0.75 / 1M tokens through 2026-12-31; $1.50 from 2027-01-01 | [Pricing](https://aistudio.google.com/docs/pricing), Gemini 3.8 Flash section |
| Output, Standard, paid | $3.75 / 1M through 2026-12-31; $7.50 from 2027-01-01. Row label: "Output price (including thinking tokens)" | same |
| Context or tier breakpoints | None listed for 3.8 Flash (no prompt-size split) | same |
| Context caching | $0.075 / 1M (then $0.15), plus storage $0.50 / 1M tokens / hour (then $1.00). Not used by us | same |
| Batch / Flex | $0.375 in, $1.875 out through 2026-12-31. Not applicable to a synchronous search call | same |
| Grounding with Google Search, paid | "5,000 free search requests per month (shared across all Gemini 3 and newer models)", then "$14 per 1,000 requests" | same |
| Grounding, free tier | "Not available" | same |
| Grounding daily cap | "default daily rate limit of 1,500 requests per day (RPD) per project across all paid tiers" | [Pricing](https://aistudio.google.com/docs/pricing); also [Rate limits](https://aistudio.google.com/docs/rate-limits) ("default limit of 1,500 requests per day (RPD) per project") |
| Model supports grounding | Yes, listed in the support table | [Grounding guide](https://aistudio.google.com/docs/google-search), [Model page](https://aistudio.google.com/docs/models/gemini-3.8-flash) (model id `gemini-3.8-flash`, thinking levels low/medium/high, `minimal` errors) |

## What is billed, and per what

**Unit.** The grounding guide says that for Gemini 3 and newer you are "billed for **each** search query that the model decides to execute", then $14 per 1,000 after the free 5,000 a month ([Grounding guide](https://aistudio.google.com/docs/google-search)). For Gemini 2.5 and older, "your project is billed per prompt." The pricing page row says "search requests" and a Gemini 4 footnote says "You will be charged for each individual search query performed." So the three pages agree on per-query for our model generation; the word "requests" in the price row means search queries, not our API calls. This is my reading, not a stated definition. Our case, one API call that issues 2 queries (`web_search_queries` had 2 entries), is therefore billed as **2 units**, not 1.

**Dynamic-retrieval note.** The tail of the pricing page says only requests that return at least one web grounding support URL are billed for Search grounding. It is not stated whether this applies to 3.8 Flash. **UNVERIFIED**; I ignore it, which can only overstate the fee.

**Thinking tokens.** Billed as output: the pricing row is labelled "Output price (including thinking tokens)", and the page says "Every token the model processes is counted and billed ... including thinking tokens" ([Pricing](https://aistudio.google.com/docs/pricing)). `thoughtsTokenCount` is defined as "Number of tokens of thoughts for thinking models" ([API reference](https://aistudio.google.com/api-reference/generate-content)).

**Tool-use and search-result tokens.** `toolUsePromptTokenCount` is "Number of tokens present in tool-use prompt(s)" and `totalTokenCount` is "prompt + tool-use prompt + thoughts + response candidates" ([API reference](https://aistudio.google.com/api-reference/generate-content)). The pricing page says tool system instructions and function declarations used for grounding are counted and billed (that wording sits in callouts for Gemini 4 models). The only statement that grounding-retrieved context is "not charged as input tokens" is in the Nano Banana 2.1 section, not 3.8 Flash. For 3.8 Flash, how tool-use tokens are billed is **UNVERIFIED**. It does not matter in practice: our measured `tool_use_prompt_token_count` is 0.

**Failed requests.** A 400 or 500 error is not charged for tokens ([Billing](https://aistudio.google.com/docs/billing)). Whether a 429 or a failed grounded call counts toward the search fee is **UNVERIFIED**.

## Free tier and limits

- **Free tier:** grounding is "Not available" on the free tier for 3.8 Flash ([Pricing](https://aistudio.google.com/docs/pricing)). This is consistent with the 429 we saw. The pages do not say the 429 message wording.
- **Paid tier:** Tier 1 is "Set up and link an active billing account"; Tier 2 is "Paid $100 + 3 days from first successful payment"; Tier 3 is "Paid $1,000 + 30 days" ([Rate limits](https://aistudio.google.com/docs/rate-limits)).
- **Per-model RPM, TPM, RPD for gemini-3.8-flash: not published.** The page says limits "vary depending on the specific model" and "can be viewed in Google AI Studio", and that rate limits "are not guaranteed". It lists no RPM for 3.8 Flash. **UNVERIFIED** (would need the AI Studio rate-limit view, which I did not open).
- **What does apply:** the grounding cap of 1,500 requests a day per project. Our cycle is about 35 requests and about 71 searches, far below 1,500 whichever way "request" is counted (calls or searches). Whether the cap counts calls or searches is not stated. **UNVERIFIED**, not binding either way. Whether 35 sequential calls hit the unpublished RPM is also unknown.

## `GoogleSearch.time_range_filter`

What the docs say, and no more:

- The generate-content reference lists `timeRangeFilter` (type `Interval`) on the `googleSearch` tool: "Optional. Filter search results to a specific time range." ([API reference](https://aistudio.google.com/api-reference/generate-content)).
- A search of the official pages returned a summary that both bounds are required if either is set, start inclusive, end exclusive. I read that only through a search summary, not on the page text I fetched, so treat it as **UNVERIFIED**. The Interval wording itself is standard but I did not see it on the page.
- The grounding guide does not mention the filter. Nothing I read says the filter is a hard guarantee, or that it is enforced on every query. The docs are silent on enforcement; our observation that it is not enforced is not contradicted or confirmed by them.
- The docs use camelCase (`timeRangeFilter`, `startTime`, `endTime`). I found no official page confirming the snake_case `time_range_filter` JSON name; the Python SDK mapping is **UNVERIFIED**.

## One-week estimate

### Inputs

From two real runs, 35 queries each, scaled to a live week. Every figure is stated here so it can be re-checked.

| Input | Per 35 queries (one cycle) | Per week: 5 cycles, 175 queries (x5) |
|---|---|---|
| Google searches (measured) | 71 (2.03 per request) | 355 |
| Prompt tokens | 8,300 | 41,500 |
| Output tokens (visible) | 10,300 | 51,500 |
| Thought tokens | 34,600 | 173,000 |
| Tool-use tokens | 0 | 0 |

Prices used: 2026 rates ($0.75 in, $3.75 out). Grounding list price $14 per 1,000 searches. News is searched only at 06:00 ET; the 12:30 cycle carries forward and costs nothing. Retries and the unmeasured analysts (technical, weather, coordinator, manager) are not included: this is the grounded-news call only.

### Token cost (separate from grounding)

- Input: 41,500 / 1,000,000 x $0.75 = **$0.0311**
- Visible output: 51,500 / 1,000,000 x $3.75 = **$0.1931**
- Thought tokens, billed as output (documented): 173,000 / 1,000,000 x $3.75 = **$0.6488**
- Tokens, thoughts billed: 0.0311 + 0.1931 + 0.6488 = **$0.8730**
- Tokens, thoughts not billed (contradicts the pricing page; shown as a floor only): 0.0311 + 0.1931 = **$0.2243**
- Same week at 2027 prices (everything x2): $1.746 thoughts billed, $0.449 not.

### Grounding fee (separate from tokens)

Searches a week = 175 requests x searches per request. Gross fee = searches x $14 / 1,000.

| Case | Searches per request | Searches a week | Gross fee at list price |
|---|---|---|---|
| Low | 1 | 175 | 175 x 0.014 = $2.45 |
| Expected (measured) | 2.03 | 355 | 355 x 0.014 = $4.97 |
| High | 3 | 525 | 525 x 0.014 = $7.35 |

If the fee were per prompt (the 2.5-and-older rule, not ours), it would be 175 x 0.014 = $2.45, the same as the Low row.

### Free-allowance effect

Searches a month = 5 cycles x 52 / 12 = 21.67 cycles x 35 requests x searches per request.

- Low: 758 requests x 1 = 758 searches a month
- Expected: 758 x 2.03 = about 1,540 searches a month
- High: 758 x 3 = about 2,275 searches a month

All are well under 5,000 a month, so the grounding fee is **$0 net** in every case, provided the allowance is ours. Headroom: 5,000 / 1,540 = about 3.2x the expected volume. The allowance is "shared across all Gemini 3 and newer models"; any other Gemini 3+ grounded calls on the same billing scope (other projects, other strategies) draw from the same 5,000. Its scope (per project, per billing account) and its reset date are **UNVERIFIED**. Once the allowance is gone, the full list-price fee applies at the gross rates above.

### Weekly totals (2026 prices)

| Case | Thoughts billed? | Tokens | Grounding, list price | Total at list price | Total with free allowance |
|---|---|---|---|---|---|
| Low | no (floor, contradicted by docs) | $0.22 | $2.45 | $2.67 | $0.22 |
| Expected | yes | $0.87 | $4.97 | $5.84 | **$0.87** |
| High | yes | $0.87 | $7.35 | $8.22 | $0.87 |

Reading: the realistic range is **about $0.87 a week with the allowance, $5.84 without it**, and **$8.22** at the pessimistic search count with no allowance. A month is about 4.33 weeks: about $3.78 with the allowance, about $25.30 without (expected case). From 2027-01-01 token cost doubles ($1.75 a week) while the grounding price is unchanged.

## Unverified items

- Whether the 5,000 free searches a month are per project, per billing account or wider, and what else consumes them.
- Per-model RPM, TPM and RPD for `gemini-3.8-flash` (not on the rate-limit page; needs the AI Studio view).
- Whether the 1,500 RPD grounding cap counts API calls or searches.
- How tool-use prompt tokens and search-result tokens are billed for 3.8 Flash specifically (our value is 0).
- Whether failed or 429 grounded calls, or calls with no web support URL, are billed for the search fee on 3.8 Flash.
- `timeRangeFilter` bound rules (both required, inclusive start, exclusive end): seen only in a search summary. The snake_case `time_range_filter` name is unconfirmed.
- Last-updated dates on the pricing, grounding and rate-limit pages (none shown). The `ai.google.dev` originals redirect, so I did not compare the two hosts directly.
- Actual billed amounts: the docs say the Cloud Console / AI Studio billing view is the authority. I did not compare our estimate to an invoice.

## What would change this estimate

- Searches per request: each extra search a request adds about $2.45 a week at list price (175 x $0.014), but $0 while under the free allowance.
- The free allowance being shared with other Gemini 3+ use, or being per-account and already consumed: adds up to $4.97 a week (expected case).
- A change in thinking level (the model supports low, medium, high). Thought tokens are 173,000 of the 265,500 weekly tokens and $0.65 of the $0.87; lowering the level could cut token cost by most of that.
- The 2027-01-01 price rise, which doubles token cost.
- Running news at the 12:30 cycle too (doubles everything), or more queries a cycle than 35.
- Retries after a held cycle, which re-run the 35 calls.
- A change to the grounding price or free allowance by Google (the pages carry no revision date).
- Adding grounded calls to other agents (weather, technical), which would add searches against the same allowance.

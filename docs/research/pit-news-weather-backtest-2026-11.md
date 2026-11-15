# Point-in-time news and weather for a multi-regime backtest, November 2026

Researched 2026-11-14. Read-only: no source code changed, nothing written to AWS or S3. Evidence comes from the owning organisation's docs, pricing pages and dataset cards, fetched during this session (URLs inline). Anything I could not verify against a primary source is marked **UNVERIFIED**. Where only a secondary source (search snippet, aggregator, third-party mirror) was found it is labelled "secondary". Several primary pages blocked or truncated my fetches (web.archive.org, CPC archive index 403, Alpha Vantage docs truncated); those are called out, not guessed.

Extends `data-sources-2026-10.md` (which covers Open-Meteo Historical Forecast and Archive, CPC ENSO, and the supply/demand feeds). Not repeated here except where the backtest changes the verdict.

Hard rule applied (CLAUDE.md, "Point-in-time data only"): a source qualifies only if past items can be retrieved exactly as first public, with a timestamp-precise `as_of`. Reanalysis, "latest revised" feeds and backfilled timestamps are out.

## Summary

| # | Source | Covers | PIT-safe? | Depth | Cost | Licence for LLM use | Effort | Verdict |
|---|---|---|---|---|---|---|---|---|
| **News** | | | | | | | | |
| N1 | GDELT 2.0 GKG/Events via BigQuery or raw 15-min files | Global news metadata: URL, themes, tone, entities. Not full text | Mostly: crawled every 15 min, stamped at ingest. Not publish time | Events/GKG 2.0 from 2015-02-19 (secondary). 1.0 GKG from 2013-04-01 | Free (BigQuery query cost only) | "100% free and open"; terms page not read | Medium (need to fetch text yourself) | Adopt as discovery layer |
| N2 | GDELT DOC 2.0 API | Article list search by keyword | Window is the last 3 months only (2017 blog), so unusable for history | 3 months | Free | as above | Low | Avoid for backtest (live use only) |
| N3 | Event Registry / NewsAPI.ai | Full-text news, 2014+, concept tagging | Has publication dates; UNVERIFIED which field is publish vs crawl | Since 2014 | Free 2,000 tokens; $90/mo 5K plan; archive 5 tokens per searched year per article search; extra tokens $0.015 | Terms not read. UNVERIFIED | Low | Adopt with caveats (best paid single source) |
| N4 | RavenPack (via WRDS or direct) | Entity-tagged news analytics, sentiment events | Yes: "point-in-time" with millisecond timestamps (WRDS, secondary-ish vendor page) | 20+ years | Institutional subscription. Price UNVERIFIED | Research terms; LLM use not stated | High (access) | Avoid unless a university/employer subscription exists |
| N5 | Common Crawl CC-NEWS | WARC dumps of news sites | Crawl time, not publish time; per-record timestamp unconfirmed | From 2016 | Free (S3 egress, compute) | Per-site copyright; not cleared | High (parse, dedupe, coverage gaps) | Avoid except as a fallback |
| N6 | Wayback Machine CDX | Snapshots of named pages (e.g. USDA, farmdoc, Reuters section pages) | Snapshot time is capture time. Content as it was then | Varies by URL | Free | Archive terms not read | Medium-high (per-site scraping) | Targeted use only |
| N7 | Alpha Vantage NEWS_SENTIMENT | Ticker/topic news with sentiment | `time_from`/`time_to` in `YYYYMMDDTHHMM`; depth not stated in docs I could read. No agri topic | UNVERIFIED | Premium plan | Not read | Low | Avoid (no agri topics) |
| N8 | Marketaux | Finance news with entity tags | Dates UTC; history depth for news not stated | UNVERIFIED (stats endpoint: 5 years weekly, 3 years daily) | Free: 100 req/day, 3 articles/request. Standard $41/mo annual, 10k req/day | Not read | Low | Avoid for history |
| N9 | FNSPID (HuggingFace) | 15.7M news for S&P 500 stocks | Date field is a string; format unverified | 1999-2023 | Free | CC BY-NC 4.0 per a mirror (secondary) | Low | Avoid (equities, not agri) |
| N10 | LSEG/Reuters, Factiva, Bloomberg | Best commodity wire coverage | Likely yes | Decades | Enterprise contracts | UNVERIFIED | High | Not researched to primary level. Out of reach |
| **Weather** | | | | | | | | |
| W1 | US Drought Monitor archive | Weekly US drought map | Yes: released Thursday, valid Tuesday | 2000 on (weekly archive) | Free | US federal | Low (already used) | Adopt |
| W2 | NOAA CPC 6-10/8-14 day outlooks | US temp and precip outlook | Yes, issuance-date archive | Dec 2001 on | Free | US federal | Low-medium | Adopt |
| W3 | CPC monthly/seasonal outlooks | US 1- and 3-month outlooks | Yes | Back to 1995 (climate.gov catalog, secondary) | Free | US federal | Low (already used) | Adopt |
| W4 | USDA NASS Crop Progress (ESMIS) | Weekly crop condition/stage, US | Yes, dated releases | 1995 on | Free | US federal | Low | Adopt (best US PIT signal) |
| W5 | NOAA GEFS on AWS (`noaa-gefs-pds`) | 21-member ensemble, 16 days, 4x daily | Yes, as-issued model runs | 1/1/2017 on (NCEI, secondary) | Free; GRIB2 heavy | Open, attribution | High | Adopt for non-US if effort is accepted |
| W6 | NOAA GFS on AWS (`noaa-gfs-bdp-pds`) | Deterministic GFS | Yes | Start date not stated on the registry page | Free | Open | High | Same as W5 |
| W7 | NOAA GEFS reforecast | 5 members daily, 2000-2019 | Hindcast with a frozen model: consistent but not as-issued | 2000-2019 | Free | Open | High | Use for climatology/skill baselines only |
| W8 | Open-Meteo Historical Forecast API | Stitched first hours of each archived run | Partial (see data-sources note). Not a lead-time forecast | ECMWF HRES from 2017-01-01; GFS 2021-03-23 | Free non-commercial; $29/mo (secondary) | CC BY 4.0 data | Low | Adopt as a "recent weather" feed |
| W9 | Open-Meteo Previous Runs API | Forecast made 1 to 7 days before valid time | Yes (true lead) | Most models Jan 2024; GFS 2 m temp Mar 2021; JMA 2018 | as above | as above | Low | Adopt for 2024+ only |
| W10 | Open-Meteo Single Runs API | One full model run | Yes | HRES 9 km from 2024-03-14; others from 2026-04-02 | as above | as above | Low | Avoid for backtest (too short) |
| W11 | ECMWF open data | Free subset of IFS/AIFS | Rolling window of last 12 runs only | No history | Free | CC-BY-4.0 | n/a | Avoid for backtest. Archive going forward |
| W12 | ECMWF TIGGE | Multi-centre ensembles | Yes, as-issued, 48 h delay | 2006 on (secondary) | Free with registration | CC BY 4.0 or CC BY-NC 4.0 per provider (ECMWF part is non-commercial) | High | Option for 2006-2019 |
| W13 | C3S SEAS5 seasonal | Monthly seasonal forecasts | Hindcast 1981-2016; real-time from 2017 | 1981 on | Free (CDS account) | Copernicus licence | High | Option for seasonal regional signal |
| W14 | OpenWeather History Forecast Bulk | 16-day forecasts, 4x daily, per location | Yes as issued, per vendor | From 2017-10-07 | Paid, price not on page | Not read | Low | Candidate if GEFS is too heavy |
| W15 | ERA5, ERA5-Land, NASA POWER, CHIRPS | Reanalysis / blended observation | **No** (see leakage) | 1940 / 1950 / 1981 | Free | Open | Low | Avoid for analyst inputs |
| W16 | Meteomatics, Visual Crossing, Weather Company, DTN, Tomorrow.io, Weatherbit | | UNVERIFIED | | | | | Not verified from primary sources (search returned nothing on archived-as-issued forecasts) |

## 1. News

### 1.1 What the pipeline does today

`agents/news.py` calls Tavily (`tools/sources/news.py`), searched on the 06:00 ET cycle only, carried forward at 12:30 (ARCHITECTURE.md, Tavily row). The date filter is at day granularity and Tavily cannot reproduce a past search. The same is true of Google grounding. Neither can be time-travelled, so a backtest needs a different news source with its own `search_news` backend, plus a `frozen_from_events`-style store.

### 1.2 GDELT

- https://www.gdeltproject.org/data.html says: "all GDELT datasets are available in Google BigQuery", 2.0 Events and GKG update every 15 minutes, 1.0 GKG begins 1 April 2013, and "The entire GDELT database is 100% free and open". The page does not state the 2.0 start date. A secondary search result gives **19 February 2015** for GDELT 2.0 and the GKG 2.1 codebook. Treat as secondary until checked in the codebook.
- The DOC 2.0 API announcement (https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) says STARTDATETIME and ENDDATETIME "must fall within the last 3 months". It is a 2017 page and the limit may have changed. **UNVERIFIED for today.** Do not plan on DOC for history.
- GKG holds metadata, themes, tone and the URL, not article text. You must fetch the article yourself, and that is where PIT breaks (below).
- **PIT leakage risks.** GDELT's timestamp is when GDELT ingested the article, which is a lag after publication but never before it. Good. The URL's content, fetched today, may have been edited or have vanished (link rot). Fetching full text through the live URL gives you the current version, not the as-published version. Mitigate: use only GKG fields (title via `V2ExtrasXML` where present, themes, tone) or fetch from Wayback by capture time near the GDELT timestamp.
- **Fit.** Good for headline-level inputs with a defensible `as_of`. Poor for "deduped, no commentary, grounded to search results" as the news agent requires, since it needs title plus snippet.

### 1.3 Event Registry (NewsAPI.ai)

- https://eventregistry.org/documentation: "Accessing the archive (data since 2014) is charged per each year of data that the query covers." Archive article search costs 5 tokens per searched year, event search 20, aggregates 10 to 50.
- https://newsapi.ai/plans: free plan 2,000 searches, a 5K plan at $90 per month, extra tokens $0.015 each if the cap is off, up to 100 articles per search. Plan-by-plan archive depth is ambiguous on the page ("confirm with sales").
- Not verifiable from these pages: whether the article date field is publish time or crawl time, retroactive edits, and licence terms for feeding articles to an LLM. All **UNVERIFIED**. Ask the vendor in writing before building on it.
- Best candidate for full-text, keyword-searchable, 2014+ news that you can query by date window, which is the shape `search_news` already has.

### 1.4 Others

- **RavenPack.** WRDS describes "point-in-time records" and "20+ years" with millisecond timestamps (https://wrds-www.wharton.upenn.edu/pages/data-announcements/now-available-enhanced-news-and-data-analytics-by-ravenpack, and the WRDS vendor page). It returns analytics (sentiment, event tags), not articles, and I found no LLM-use terms. Price UNVERIFIED. Only realistic with an academic or employer licence.
- **Common Crawl CC-NEWS.** https://commoncrawl.org/blog/news-dataset-available states file lists "from 2016 to-date", daily WARCs, and "released at an early stage in its development". Whether each record carries a fetch timestamp is not stated on that page. Coverage is a crawler's choice of sites, so agri-wire gaps are likely. Avoid as primary.
- **Wayback CDX.** Per https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md: `from`/`to` filter by capture timestamp (to 14 digits), `collapse=timestamp:10` keeps one capture per hour, `digest` identifies unchanged content, default cap of 150,000 results per query, no rate-limit statement. My direct fetches of web.archive.org were blocked in this environment, so I could not test coverage. Use it to rebuild a few known index pages (USDA, farmdoc daily, ICO/ICCO news pages) at a given `as_of`, not as a news search.
- **Alpha Vantage NEWS_SENTIMENT.** The docs (https://www.alphavantage.co/documentation/) show `time_from`/`time_to` in `YYYYMMDDTHHMM`, topics limited to a fixed list with no agriculture topic (closest: `energy_transportation`, `economy_macro`), and mark the function premium. History depth is not stated in the section. **UNVERIFIED.**
- **Marketaux.** https://www.marketaux.com/documentation: `published_after`/`published_before` UTC; the stats endpoint looks back at most 3 years daily and 10 years quarterly. Article-level depth on plans not stated. Free tier 100 requests per day with 3 articles per request (pricing page, secondary snippet). Equity-oriented.
- **FNSPID.** 15.7M news for S&P 500 names, 1999-2023 (https://github.com/Zdong104/FNSPID_Financial_News_Dataset). Equity only, licence CC BY-NC 4.0 per a mirror (secondary). Not useful for grains or softs.
- **Not covered.** Benzinga, Polygon, Nasdaq Data Link, Kaggle sets, DTN, Barchart, farmdoc and USDA press-release archives, Reuters/LSEG, Factiva and Bloomberg were not verified from primary pages in this session. farmdoc daily and USDA press releases are dated web posts and can be rebuilt through Wayback, but I did not check their archive structure.

### 1.5 News leakage risks (all sources)

1. **Retroactive edits.** Web articles are updated after publication (corrections, appended "update" paragraphs, changed headlines). Any source that returns text fetched later than `as_of` leaks. A date field is not a content version.
2. **Backfilled timestamps.** Datasets that bulk-load history (compare the CFTC finding in the October note, rows with `:created_at` in 2022 for 2015 data) can stamp ingest, not publication. Vendors rarely document this.
3. **Crawl vs publish time.** CC-NEWS and GDELT stamp capture time. Safe direction (later than publish), but day-granularity filtering in `search_news` would admit same-day items published after the cycle. Filter on the timestamp, not the date.
4. **LLM knowledge leakage.** The bigger risk. Research on this: "The Memorization Problem" (https://arxiv.org/html/2504.14765v2) reports models recall exact economic and financial values before their cutoff and that masking entities often fails because models reconstruct them from minimal context. Gao, Jiang, Yan (https://arxiv.org/pdf/2512.23847v1) propose a test for look-ahead bias in LLM forecasts. A May 2026 paper (https://arxiv.org/pdf/2605.24564) names "parametric look-ahead bias" that lives in weights, not inputs. These are preprints, not peer reviewed. A 2012 drought or the 2020-2022 rally is widely documented, so a model that saw it will "know" the outcome. Dates stripped from headlines will not fix that.
   - CLAUDE.md already lists the mitigations (cutoff-predating model, stripped dates, forward testing). The strongest design is a **split test**: periods before the model's cutoff measure pipeline mechanics, only periods after the cutoff measure skill. The DeepSeek model's cutoff should be checked against its model card; I did not research it.

## 2. Weather

### 2.1 What the pipeline uses today

`tools/sources/weather.py`: US Drought Monitor (state statistics API), NOAA CPC monthly outlook (shapefile), Open-Meteo live forecast for non-US regions (past 14 days plus next 7). The live Open-Meteo forecast is not an archive, so the backtest needs a replacement for non-US regions. The US inputs are already PIT.

### 2.2 US sources (strong)

- **Drought Monitor.** Weekly archive to 2000 and release Thursday, valid Tuesday (secondary: drought.gov and USDA pages from search results; NDMC's own data-download page did not state it and the NDMC publications page was a 404). The pipeline already reads it as "never-revised" (ARCHITECTURE.md). I could not verify that statement from an NDMC primary page this session. If the Monitor has ever retroactively re-authored past weeks, the downloadable archive is the revised one; check before relying on it. **UNVERIFIED.**
- **CPC outlooks.** CPC's own archive page says 6-10 and 8-14 day data and graphics go back only to **December 2001**, looked up by issuance date (https://cpc.ncep.noaa.gov/products/archives/short_range/srarc.ind.php, via search result; my direct fetch of the CPC archives index returned 403). The one-month and three-month maps go back to 1995 (climate.gov catalog, secondary). GIS shapefile depth for the 6-10/8-14 products could not be confirmed. The current monthly-outlook loader uses `ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/monthlyupdate`, whose history depth I did not check.
- **NASS Crop Progress.** https://esmis.nal.usda.gov/publication/crop-progress: release archive from April 1995, weekly April to November, PDF/TXT/ZIP, upcoming releases at 4:00 PM. Dated releases are immutable files. This is the best PIT signal for US corn, soybeans, wheat and cotton condition and is not yet used by the weather agent. Strong candidate.

### 2.3 Non-US forecasts

For Brazil (coffee, sugar), West Africa (cocoa), Vietnam/Colombia (coffee), India/Thailand (sugar), the only truly as-issued data are archived NWP runs.

- **NOAA GEFS on AWS.** Bucket `noaa-gefs-pds`, us-east-1 (https://registry.opendata.aws/noaa-gefs/): 21 members, 16 days, 4 runs a day, open licence with attribution. The registry page states no start date. An NCEI page (secondary) says 2017-01-01 to present. Layout `gefs.YYYYMMDD/HH/pgrb2a|pgrb2b/` with `gec00` control and `gepNN` members (secondary, docs.opendata.aws search result). My fetch of the docs page itself returned 404, so file naming is **UNVERIFIED**. Highest-quality as-issued ensemble, but the model changed versions (GEFS v12 in 2020, secondary) so skill is not stationary.
- **NOAA GFS on AWS.** Bucket `noaa-gfs-bdp-pds` (https://registry.opendata.aws/noaa-gfs-bdp-pds/). Start date **not stated** on the registry page. Note a v16.3.0 upgrade on 2022-11-29, which is a model-version break for any skill estimate.
- **GEFS reforecast.** https://registry.opendata.aws/noaa-gefs-reforecast/: 2000 to 2019, mostly 5 members daily, 11 members weekly to +35 days, from a frozen model. It was not issued at the time (it is a hindcast), so it is consistent but not as-issued. Good for 2012-drought-style tests of "what would a modern ensemble have said", but that is a different claim from "what the world knew then".
- **Open-Meteo** (page details in the October note, not repeated). New facts: Previous Runs (https://open-meteo.com/en/docs/previous-runs-api) gives `previous_day1` to `previous_day7` and starts January 2024 for most models, March 2021 for GFS 2 m temperature, 2018 for JMA GSM/MSM; "earlier history can be reconstructed on request". Single Runs (https://open-meteo.com/en/docs/single-runs-api) archives from 2026-04-02 for most models and 2024-03-14 for IFS HRES 9 km. So Open-Meteo gives true-lead forecasts only for about 2.5 years (Previous Runs, 2024 on). Earlier history is stitched analysis-like data.
- **ECMWF open data.** https://www.ecmwf.int/en/forecasts/datasets/open-data: CC-BY-4.0, but "Data are retained for the most recent 12 forecast runs". It cannot supply history. It would, however, let the project archive its own copy going forward.
- **ECMWF TIGGE.** Archive from October 2006 (secondary), 48-hour delay, licence CC BY or CC BY-NC depending on the provider, with ECMWF's part non-commercial (https://ewds.climate.copernicus.eu/licences/tigge-licence, via search). The page I fetched (https://confluence.ecmwf.int/display/TIGGE/Models) states the grid resolution and a move to a common 0.25°/0.5° grid from 1 October 2026, but not the start date. Older runs are on coarse grids. Heavy GRIB, and CDS-style queue access.
- **C3S SEAS5 seasonal.** Hindcast 1981-2016, real-time from 2017 (secondary: ECMWF EQC pages), Copernicus licence. Useful for the "seasonal signal" such as El Nino rainfall anomalies over a region, not for 1-week calls.
- **OpenWeather History Forecast Bulk.** https://openweathermap.org/api/history-forecast-bulk: from 2017-10-07, 16 days, hourly, 4 runs a day, per-location bulk orders, paid (price not on the page). The vendor does not say which model it is. Candidate but opaque.
- **Others** (Meteomatics, Visual Crossing, Weather Company, DTN, Tomorrow.io, Weatherbit): a search found nothing on archived-as-issued forecasts for these. **UNVERIFIED**; do not assume they offer it.

### 2.4 Reanalysis leakage (flag, do not use as an input)

- **ERA5 / ERA5-Land** (Open-Meteo Archive API, October note): a reanalysis assimilates observations after the fact, so it knows rainfall that actually fell. It leaks the realized weather into any "forecast". Fine as a climatology or ground-truth label for scoring.
- **NASA POWER, CHIRPS:** both blend satellite and station observations after the fact. Not fetched from primary pages this session; their reanalysis/observation-blend nature is from my own background knowledge, so mark as **UNVERIFIED** here. Treat as labels, not inputs.
- **ONI/RONI** index files are re-derived (October note); use the monthly CPC ENSO discussion instead.

### 2.5 Weather leakage risks

1. Stitched "historical forecast" series are mostly hour 0 to a few of each run, close to an analysis, and not a lead-time forecast (October note).
2. Model changes (GFS v16.3.0 on 2022-11-29; IFS cycle 50R1 in May 2026 per Open-Meteo) change skill mid-backtest. A "regime" result may partly be a model-version result.
3. The coordinator may use the weather analyst's "regional label" to call a drought year it remembers. See 1.5 item 4.

## 3. Recommended stacks

### News (all regions, one source)

1. **Primary: Event Registry**, if its terms allow LLM use and its date field is publish time (ask first). Query per commodity per month in bulk and store everything to S3 with `fetched_at`, so reruns are frozen the way `frozen_from_events` already works.
2. **Fallback / cross-check: GDELT GKG (BigQuery)** for headline, theme and tone counts. Cheap, deep, defensible timestamps.
3. **Scoped extras via Wayback:** capture-time-limited snapshots of USDA and farmdoc index pages if specific events matter.
4. **No full-text re-fetch from live URLs.**

### Weather per region

| Commodity / region | Backtest input | Depth |
|---|---|---|
| Corn, soy, wheat, cotton, US sugar (US states) | USDM (W1) + CPC outlooks (W2/W3) + **NASS Crop Progress (W4)** | 2001/2000 to now |
| Coffee, sugar (Brazil) | GEFS (W5) 2017+ for 7-16 day rain/temp, with CPC ENSO discussion and C3S SEAS5 for seasonal context | 2017+ |
| Cocoa (West Africa) | GEFS (W5) 2017+. No independent drought index | 2017+ |
| Coffee (Vietnam, Colombia), sugar/cotton (India, Thailand) | GEFS (W5) 2017+. Open-Meteo Previous Runs only for 2024+ | 2017+ |
| Pre-2017, non-US | TIGGE (W12) if willing to pay the integration cost, else run weather-off | 2006+ |

## 4. Gaps no source fills

- **Non-US PIT observations or drought indices before 2017.** No free equivalent of USDM/Crop Progress for Brazil, West Africa, Vietnam, India. Forecast-only.
- **Pre-2017 as-issued NWP at usable lead times** beyond TIGGE's coarse grids. Open-Meteo true-lead history is about 2024+.
- **Publish-time-exact commodity wire news** (Reuters, Bloomberg, Dow Jones) without enterprise licences. I could not verify price or LLM-use rights for any of them.
- **Content-version control.** No source verified here guarantees an article is the text as first published.
- **LLM memory.** Not a data gap, but no data source removes it (1.5 item 4). Stripping dates is not enough per the memorization literature.
- **Licensing for LLM use.** Not stated in any page I read for Event Registry, GDELT's linked terms, RavenPack. Needs vendor confirmation.

## 5. Effort and cost for a ~10-year backtest (my estimates, not vendor figures)

Scale: about 2,500 trading days x 2 cycles x 7 commodities, so about 35,000 commodity-cycles. News is searched once a day (06:00 cycle): about 2,500 x 7 x 5 tool calls, about 88,000 searches in the live pattern.

| Item | Estimate | Basis |
|---|---|---|
| Event Registry, naive per-cycle queries | Up to 88k searches x 5 tokens = about 440k tokens, about $6,600 at $0.015/token | Unit price is vendor-stated. Assumes a one-day window is charged as one "searched year", which is **UNVERIFIED** |
| Event Registry, bulk-by-month per commodity | Far lower: a few hundred searches, then local filtering. Plan fee $90 to a few hundred | Needs a local store and a `search_news` replacement |
| GDELT BigQuery | Likely low tens of USD of scan cost | UNVERIFIED, depends on GKG scan size |
| NOAA GFS/GEFS storage and compute | Free source. Engineering cost dominates: GRIB decoding, point extraction for about 20 regions x 4 runs x 16 days | Engineering estimate: 1 to 2 weeks |
| USDM, CPC, NASS | Near zero beyond loaders; NASS is new work, about 2 to 3 days (PDF/TXT parse) | |
| News adapter + frozen store + date-filter fix | About 1 week | |
| LLM spend | Already measured from live logs; scale with commodity-cycles (35k). Not estimated here | |

Plan phases: US-only grains/cotton first (all sources free and PIT, 2001+), then softs with GEFS from 2017.

## Ranked recommendation

1. **Backtest the US-weather commodities first (corn, soy, wheat, cotton).** USDM, CPC, and NASS Crop Progress are free, PIT, and cover 1995-2001 onward, covering 2012, 2020-22 and more. Add NASS Crop Progress to the weather analyst; it is the cheapest quality gain.
2. **Pick one archived news source with a contractual answer on publish-time and LLM use: Event Registry**, loaded in bulk into an S3 store, not queried per cycle. GDELT GKG as the free cross-check.
3. **Add GEFS (AWS) as the non-US forecast backtest source from 2017**; accept the engineering cost. Before 2017, run non-US weather as an explicit "off" ablation instead of faking it.
4. **Treat Open-Meteo Previous Runs as a 2024+ convenience only**, and do not use Historical Forecast, ERA5, NASA POWER or CHIRPS as PIT inputs.
5. **Design for LLM leakage first:** split periods by the model's training cutoff, compare against the technical-only baseline, and report news-off and weather-off ablations so the result does not depend on a source with a weaker PIT guarantee.
6. Skip RavenPack, Factiva and LSEG unless an institutional subscription already exists.

Open items to verify before building: Event Registry publish-vs-crawl field and LLM licence; Wayback fetch access from this environment; NDMC statement on whether past USDM weeks are ever revised; GFS archive start date on `noaa-gfs-bdp-pds`; CPC GIS shapefile archive depth for 6-10/8-14 products; the DeepSeek model's training cutoff.

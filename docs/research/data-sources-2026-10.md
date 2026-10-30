# Candidate data sources: point-in-time (PIT) audit, October 2026

Researched 2026-10-30. Read-only: no source code changed, nothing written to AWS or S3. Evidence comes from the owning organisation's own pages, files and endpoints, fetched during this session. Anything I could not verify against a primary source is marked **UNVERIFIED**. Where only a secondary source was found it is labelled "secondary".

Hard rule applied: a source qualifies only if past releases can be retrieved exactly as first published. "Latest revised values" feeds are out.

## Summary

| # | Source | Free? | PIT-safe? | Effort | Verdict |
|---|---|---|---|---|---|
| 1a | Open-Meteo Historical Forecast API (archived model output) | Free for non-commercial use only; paid plan for commercial use | Partial: archived, not re-analysed, but stitched from each run's first hours and model versions change | Low | Adopt with caveats |
| 1b | Open-Meteo Archive API (ERA5 / ERA5-Land) | Same | No: reanalysis, and ERA5 has a 1940 start | Low | Avoid for anything PIT-graded |
| 2 | USDA FAS Coffee: World Markets and Trade | Yes | Yes (dated editions, 2 a year) | Medium | Adopt with caveats. Fix the parser and the URL source |
| 3 | USDA FAS Sugar: World Markets and Trade | Yes | Yes (dated editions, 2 a year) | Medium | Adopt with caveats |
| 4a | USDA FAS cocoa report | n/a | n/a | n/a | No such report found. Drop from the scraper |
| 4b | ICCO Quarterly Bulletin | Paid (statistics section sells it) | Likely yes but paywalled | Low | Avoid for now |
| 4b | ICCO monthly Cocoa Market Report | Free web articles | Yes (dated posts) | Medium (prose) | Adopt with caveats |
| 4c | ECA, NCA, Cocoa Association of Asia quarterly grinds | Free | Mostly yes (dated PDFs). NCA revises (a "REV" file exists) | Low | Adopt |
| 4d | Ivory Coast port arrivals | No free official feed found | n/a | n/a | Avoid. Use ICCO monthly text |
| 5 | CFTC Commitments of Traders (Public Reporting API) | Yes | Yes from now on (row `:created_at` = release time). History is bulk-loaded, so no release stamps | Low | Adopt |
| 6 | NOAA CPC ENSO discussion | Yes | Yes (dated pages). The ONI/RONI tables are not PIT | Low to medium | Adopt the discussion, avoid the index files |
| 7a | UNICA bi-weekly Centre-South report | Free, citing UNICA is required | UNVERIFIED | Medium | Adopt with caveats, archive ourselves |
| 7b | CONAB coffee and sugarcane surveys | Free | Yes (numbered surveys with dates) | Medium (Portuguese PDF/XLSX) | Adopt with caveats |
| 8 | ICE certified stocks | Not confirmed free. EOD packages are paid | UNVERIFIED | Unknown | Avoid until verified. If free, archive daily |

## 1. Open-Meteo

**URLs.** Historical Forecast API docs at https://open-meteo.com/en/docs/historical-forecast-api. Endpoint: `https://historical-forecast-api.open-meteo.com/v1/forecast`. Archive API docs at https://open-meteo.com/en/docs/historical-weather-api. Endpoint: `https://archive-api.open-meteo.com/v1/archive`. Previous Runs API docs at https://open-meteo.com/en/docs/previous-runs-api.

**PIT behaviour.**
- The Historical Forecast API provides "historical forecasts archived from the Weather Forecast API" with the same models and parameters. "Each run's first few hours are stitched into a continuous hourly timeseries." So you get what the model said for hours 0 to a few after each run, which is close to an analysis. It is not the full forecast as issued.
- The page warns it is "not suitable for long time series due to model version changes over time." It does not state how many lead hours are used per run. UNVERIFIED.
- The Previous Runs API returns values at a fixed lead of 1 to 7 days. This is the closest match to "forecast as it stood then", but most models start only in January 2024.
- The Archive API serves ERA5 (about 25 km, from 1940, about 5 days delay) and ERA5-Land (about 11 km, from 1950). ERA5 is a reanalysis. The docs say nothing about revisions or ERA5T, but a reanalysis is by nature not "as first published". Do not use it for graded cycles. It is fine for a climatology baseline.

**Per-model start dates (Historical Forecast docs).**

| Model | Start |
|---|---|
| ECMWF IFS HRES (9 km) | 2017-01-01 |
| ECMWF IFS 0.4° | 2022-11-07 |
| ECMWF IFS 0.25° | 2024-02-03 |
| NOAA GFS | 2021-03-23 |
| DWD ICON | 2022-11-24 |

I tested the endpoint for Dak Lak with `models=ecmwf_ifs`, `ecmwf_ifs04` and `ecmwf_ifs025`. All three work for 2023-01-02. For 2020-01-02 only `ecmwf_ifs` returns values; the other two return nulls. This matches the docs. Pin an explicit model, since the default "best match" changes over time.

**Coverage.** The models are global. The API accepted all seven test points: Minas Gerais (-21.2, -45.0), Dak Lak (12.67, 108.04), Huila (2.5, -75.5), Ivory Coast (6.8, -5.3), Ghana (6.7, -1.6), Maharashtra (19.0, 75.0) and Thailand (15.0, 100.0). Sao Paulo, Espirito Santo and the Centre-South are inside the same global grids. I did not test values at each point. Karnataka was not tested but lies between the tested points.

**Licence and limits.**
- Free API: "for non-commercial use" (https://open-meteo.com/en/pricing, https://open-meteo.com/en/terms). Limits are 600 calls a minute, 5,000 an hour, 10,000 a day and 300,000 a month. They apply per IP and per application.
- Allowed on free: private or non-profit use with no ads or subscriptions, public research at public institutions, personal use. Not allowed: commercial products, and "undisclosed research at commercial entities".
- Paid: Standard (1M calls a month), Professional (5M) and Enterprise (50M+). A subscription grants a commercial licence. Prices are not listed on the page. UNVERIFIED.
- Data licence is CC BY 4.0, so attribution is required.

**Does this project need a paid plan?** The project is a research and paper-trading tool, but the owner runs it privately and the output informs trading decisions. The terms exclude "commercial products" and "undisclosed research at commercial entities". Whether personal paper trading counts is a judgement call and is not defined by Open-Meteo. The conservative reading is that a paid plan is needed if the results ever feed real money. At two cycles a day and a few dozen points the call volume is far below free limits, so the cost would be the plan fee only.

**Parse difficulty.** Low (JSON).

**Verdict: adopt with caveats.** Use the Historical Forecast API (or Previous Runs for lead-time fidelity), pin the model, ignore ERA5 for graded runs. Archive our own pulls from now on, because model changes make later re-queries differ. Settle the licence question before any real-money use.

## 2. USDA FAS "Coffee: World Markets and Trade"

**Cadence.** Two editions a year, June and December (ESMIS: "published in June and December", https://esmis.nal.usda.gov/publication/coffee-world-markets-and-trade). Actual dates seen: 2024-06-20, 2024-12-18, 2025-06-25, 2025-12-18 (PDF creation stamp 20251218), and a **July 22, 2026** edition (title page "July 2026", creation stamp 20260722). So the "June 2026" edition came out in July.

**Is a 2026 release available as of Oct 2026?** Yes, but not where our scraper looks.
- ESMIS (https://esmis.nal.usda.gov/publication/coffee-world-markets-and-trade, fetched with curl) lists releases only up to **Jun 25 2025**. It has no Dec 2025 or 2026 edition. The Cornell mirror (usda.library.cornell.edu) also stops at 2025-06-25 on the pages I could see. Why ESMIS stopped is UNVERIFIED.
- The newer editions are on `https://apps.fas.usda.gov/psdonline/circulars/YYYY/MM/coffee.pdf`. I fetched:
  - `2025/06/coffee.pdf` = the Jun 2025 edition (same bytes as the ESMIS file).
  - `2025/12/coffee.pdf` = Dec 18 2025 edition.
  - `2026/07/coffee.pdf` = Jul 22 2026 edition.
- The folder behaves as "latest edition at or before that month". `2026/06/coffee.pdf` returns the same bytes as `2025/12`, and `2026/08` and `2026/10` return the July 2026 file. Always read the date from the PDF title page or metadata, not from the path.
- The FAS pages (www.fas.usda.gov/data/coffee-world-markets-and-trade and the `report-release-announcement/...` pages) return HTTP 403 to curl and WebFetch. I could not read the FAS release calendar. UNVERIFIED.

**Why the scraper returns the 2025-06-25 release.** `_COFFEE_LISTING` points at ESMIS, and ESMIS has nothing newer. That part is a source-staleness problem, not a regex problem. `_COFFEE_RELEASE_RE` matches all 9 files on page 0 of the listing (I checked: nine `coffee.pdf` links, newest 2025-06-25). Page 1 of the listing (older releases) is never fetched, which is fine here.

**Why the figures look garbled (root cause).** I downloaded the Jun 2025 and Dec 2024 PDFs and ran `_parse_coffee` on them.

| Edition | Parser output | Correct world total ending stocks / use |
|---|---|---|
| Jun 2025 (latest) | 22,819 and 13.47 | 22,819 / 169,363 = 13.47. **Correct.** |
| Dec 2024 (prior) | 97,845 and 58.22 | 20,867 / 168,071 = 12.42. **Wrong.** |

So the latest figure is right and the **prior** is garbled. The change then compares a correct number with a wrong one.

The cause is in `_parse_coffee`. It sets `section = "ending_stocks"` on **any line containing "Ending Stocks"**. The Dec 2024 PDF has a headline on page 1 at extracted line 120: "World Ending Stocks Continue to Tighten". That flips `section` early, before the real tables. The first world "Total" row after that is the **Arabica production** total (line 288: `102,120  Total 87,100 87,779 96,379 99,855 97,845`). The parser takes its last column, 97,845, as ending stocks. The use total is picked up correctly later (168,071), giving 97,845/168,071 = 58.22.

The Jun 2025 PDF has no such headline, so it parsed correctly by luck. The same bug hits the Jul 2026 edition. Its headline at line 117 contains "Ending Stocks", and the parser returns 105,867 and 58.9, which is implausible. The Dec 2025 edition parses correctly (20,148; 11.59%).

**Secondary issue.** `marketing_year` is the last `\d{4}/\d{2}` found on any line before the first section trigger. It is body text, not the table header, so it is fragile.

**Suggested fix (not applied).**
1. Anchor the section markers to the table row labels, which read exactly `Domestic Consumption none` and `Ending Stocks none` in all four PDFs.
2. Take the marketing year from the table header line.
3. Add a sanity check, for example reject stocks-to-use above 40%.
4. Add a regression test with the Dec 2024 and Jul 2026 PDFs.
5. Switch the listing source from ESMIS to the `apps.fas.usda.gov/psdonline/circulars/YYYY/MM/coffee.pdf` pattern, or archive PDFs ourselves, and read the release date from the PDF.

**PIT-safe?** Yes. Each edition is a dated, immutable PDF. **Access:** free, no key. **Parse difficulty:** medium (PDF text extraction, but the layout is stable: a leading column printed left of the country name, then the remaining columns; the last column is the newest forecast). **Verdict: adopt with caveats.** Fix the parser and the source before relying on it.

## 3. USDA FAS "Sugar: World Markets and Trade"

- ESMIS listing: https://esmis.nal.usda.gov/publication/sugar-world-markets-and-trade. It shows 10 releases from 2020-05-21 to 2025-05-22, in May and November. It is stale after May 2025, like coffee.
- Newer editions are at `https://apps.fas.usda.gov/psdonline/circulars/YYYY/MM/sugar.pdf`. The Dec 2025 edition has creation stamp 2025-12-16 and title "December 2025". The November 2025 edition therefore slipped to mid-December. A May 2026 edition has stamp 2026-05-28. The Dec 2025 PDF states "The next release of this circular is scheduled for May 28, 2026" and points to https://www.fas.usda.gov/data/sugar-world-markets-and-trade for archived and future releases.
- Cadence is therefore May and November, with slips. I found no later edition for Nov 2026 yet (the `2026/10` path still returns the May 2026 file).
- Format: PDF with pages titled "World Centrifugal Sugar: Production and Consumption / Imports and Exports / Ending Stocks", in 1,000 metric tons raw value, with the same layout as the coffee report (leading column left of the label, "none" after the section name). `_parse_coffee` returns `None` on sugar, so a new parser is needed; reuse the anchored-section approach from item 2.
- Note that the project already takes **US** sugar from WASDE (`sr16`). This report adds the world balance (Brazil, India, Thailand, EU exports, ending stocks).

**PIT-safe?** Yes (dated editions). **Parse difficulty:** medium. **Verdict: adopt with caveats.**

## 4. Cocoa supply and demand

**4a. USDA FAS.** `https://esmis.nal.usda.gov/publication/cocoa-world-markets-and-trade` returns HTTP 404. A web search found no FAS cocoa "World Markets and Trade" report. FAS does not appear to publish one; none of the three FAS publication families I could confirm (coffee, sugar, WASDE) include cocoa. I could not read the FAS site itself (403), so this is "not found", not proof of absence. In our code, `report_for_symbol("cocoa")` falls through to `"wasde"` and `_WASDE_TABLES` has no cocoa entry, so `get_report` returns `None`. That explains "gets nothing". **Verdict: drop the call for cocoa.**

**4b. ICCO** (https://www.icco.org).
- Quarterly Bulletin of Cocoa Statistics (https://www.icco.org/statistics/quarterly-bulletin-of-cocoa-statistics/): issued four times a year (February, May, August, November). Data goes back to 1960. It is bought through the ICCO online shop or an order form; contact is statistics.section@icco.org. Only sample index PDFs are free. The page gives no price or archive. Paid, **avoid for now**.
- Monthly Cocoa Market Report (e.g. https://www.icco.org/cocoa-market-report-for-march-2025/): a free web article with price averages, supply and demand commentary, and cumulative Ivory Coast arrivals. The March 2025 edition is dated Abidjan, 11 April 2025, so it lags by about 6 weeks. Archive index at icco.org/icco-documentation/cocoa-market-review/. Prose, so it needs an LLM or regex. PIT-safe by post date. **Adopt with caveats.**

**4c. Grind data (free, quarterly, 2 weeks after quarter end).**

| Body | URL | Timing | Archive |
|---|---|---|---|
| ECA (Europe) | https://www.eurococoa.com/all-about-grind-stats/ and `.../wp-content/uploads/WEBSITE-REPORT-WESTERN-STATS-Q3-2026.pdf` | Q3 2026 published Oct 15 2026 8:00 CET; Q4 scheduled Jan 21 2027 | Stats retroactive to Q1 1999; the page lists 2026 dates; archive link `eurococoa.com/grind-stats/` not opened (UNVERIFIED) |
| NCA (North America) | https://candyusa.com/cocoa-grinds-report/ and PDFs like `.../2026/04/Q1-2026-Cocoa-Grinds.pdf` | Q1 Apr 16, Q2 Jul 16, Q3 Oct 15 2026, Q4 Jan 21 2027 | Dated PDFs per quarter. A file named `Q2-2025-Cocoa-GrindsREV.pdf` exists, so figures can be revised and the original may not be kept |
| Cocoa Association of Asia | https://www.cocoaasia.org/grinding-figures | Dates on page: Jan 16, Apr 16, Jul 16, Oct 15 2026, after 4 pm Singapore time | 2012 Q1 to 2026 Q3; mostly PDFs, a few images (2019 Q1-Q4, 2024 Q3) |

PIT-safe: yes for first publication dates, with the NCA revision caveat. Archive ourselves at release. **Verdict: adopt.** I did not open the Q3 2026 NCA or Asia files; the only Q3 2026 number I saw (Europe 348,215 t, +3.2%) is from a German trade article (secondary, UNVERIFIED).

**4d. Ivory Coast port arrivals.** I found no free official weekly series. The regulator is the Conseil du Café-Cacao, and its site was not checked. Public figures reach us through exporter data cited by Reuters (secondary) and the ICCO monthly report. **Verdict: avoid as a structured feed.** Use ICCO monthly text for cumulative arrivals.

## 5. CFTC Commitments of Traders

- Landing page: https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm. Reports are "generally released Fridays at 3:30 p.m. Eastern", with data as of the preceding Tuesday. Holidays shift the date. The 2026 release schedule (https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm) marks delayed releases on Jan 5, Jun 22, Jul 6, Nov 16, Nov 30 and Dec 28. The site keeps about 13 months of release dates only.
- The page says "historical data is not updated once published". It describes no resubmission process.
- Zip archive: https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm. Legacy futures-only from 1986, Disaggregated from 2006 (the 2006-2016 file, then yearly), TFF, and CIT. File names such as `fut_disagg_txt_YYYY.zip` and `deacotYYYY.zip`.
- API (Socrata, Public Reporting Environment): disaggregated futures-only `72hh-3qpy`, disaggregated combined `kh3c-gbw2`, legacy futures-only `6dca-aqww`, legacy combined `jun7-fc8e`, TFF `gpe5-46if` and `yw9f-hn96`, CIT `4zgm-a668`. The endpoint is `https://publicreporting.cftc.gov/resource/<id>.json`. I queried `72hh-3qpy`: reports run from 2006-06-13 to 2026-10-20.
- **PIT detail.** The Socrata system column `:created_at` for recent rows equals the release time. The latest coffee row (report date 2026-10-20) has `:created_at` 2026-10-23T19:30:09Z, which is Friday 3:30 pm EDT. A 2015 row has `:created_at` 2022-08-01, so history was bulk-loaded and carries no release stamp. For history, assume the Friday after the Tuesday report date (apply the holiday schedule), which is the same rule the zip files imply. Store our own pull per release from now on.

**Ag contracts** (disaggregated, from the live dataset):

| Contract | Code |
|---|---|
| Sugar No. 11 (ICE US) | 080732 |
| Coffee C (ICE US) | 083731 |
| Cotton No. 2 (ICE US) | 033661 |
| Cocoa (ICE US) | 073732 |
| Corn (CBOT) | 002602 |
| Soybeans (CBOT) | 005602 |
| Wheat SRW (CBOT) | 001602 |
| Wheat HRW (CBOT; the "KC" wheat) | 001612 |

Free, no key needed for modest volume (an app token is advisable). Parse difficulty: low. **Verdict: adopt.** Use the Socrata API with a release-date rule for history.

## 6. NOAA CPC ENSO

- Current discussion: https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/index.shtml. The Oct 2026 issue is dated 8 October 2026 (second Thursday), status "El Niño Advisory", next issue 12 November 2026.
- Archive: one page per month at `https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_disc_<mon><yyyy>/ensodisc.shtml`. Oct 2024 returns 200 and the search index also shows Nov 2006 and Oct/Nov 2016 pages at similar paths. The path pattern did not resolve for jan2010, jul2002 or feb2000, so the pattern may differ for older issues. The archive index page (`ENSO_DD_archive.php`) exists; the `.shtml` URL says "replaced or moved". How far back it goes: UNVERIFIED (one cached version showed years from 2001).
- The discussion is dated HTML and PDF prose. PIT-safe by issue date.
- Index files at https://www.cpc.ncep.noaa.gov/data/indices/: `oni.ascii.txt` (ERSSTv6, centred base periods) and `RONI.ascii.txt` (relative ONI, 1991-2020 base). The ONI page says RONI is now used for official monitoring. The last row of `oni.ascii.txt` is JAS 2026 and the file was modified 3 Oct 2026. These tables are re-derived from a re-analysed SST product and the base periods are re-centred, so past values are **not** PIT. Keep our own dated snapshot if we use them.
- **Verdict: adopt the monthly discussion (status and probabilities); avoid the index tables unless snapshotted at fetch time.** Effort low to medium (HTML scrape).

## 7. Brazil: UNICA and CONAB

**7a. UNICA.**
- Site: https://unicadata.com.br (Portuguese, English toggle `?idioma=2`). It covers crop monitoring, crush, sugar and ethanol production, and exports. No login or terms were visible. Download formats and the archive of past releases were not stated on the home page. UNVERIFIED.
- The bi-weekly "Acompanhamento quinzenal da safra na região Centro-Sul" is a PDF with cumulative-to-date and fortnight tables. Copies exist at novacana.com (secondary), with file names such as `Unica-141125_NC.pdf` and `unica-acompanhamento-161225.pdf`. Each PDF says partial or full reproduction is allowed when UNICA is cited.
- The dynamic history tables on unicadata are season aggregates, not per-release. UNICA restates earlier fortnights in later reports. I could not confirm this from a primary source. UNVERIFIED, but plan for it.
- **PIT-safe: UNVERIFIED.** Treat the unicadata tables as non-PIT. **Verdict: adopt with caveats.** Ingest each fortnightly PDF on release and keep it; do not use the history tables for backtests.

**7b. CONAB.** Pages under https://www.gov.br/conab/pt-br/ (the old conab.gov.br paths redirect there).
- Coffee: four surveys a year (page text: "quadrimestralmente", four editions). Listed: 4th Safra 2025 on 04/12/2025, 1st Safra 2026 on 05/02/2026, 2nd on 21/05/2026, 3rd on 24/09/2026, all at 09:00. Older editions via a "Mais..." link. Page: https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/safra-de-cafe.
- Sugarcane: 3rd Safra 2025/26 on 04/11/2025, 4th on 17/04/2026, 1st Safra 2026/27 on 28/04/2026, 2nd on 20/08/2026. Page: https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/safra-de-cana-de-acucar.
- File formats were not stated on the pages (PDF boletim plus XLSX are typical but UNVERIFIED). Language: Portuguese.
- PIT-safe: yes in principle (numbered, dated surveys kept in an "arquivos-boletins" area). I did not verify that old files are unchanged. **Verdict: adopt with caveats.**

## 8. ICE certified stocks

- ICE Report Center: https://www.ice.com/report-center (redirected from theice.com). Pages exist at `https://www.ice.com/report/41` (Certified Stock Reports - Cocoa), `/report/42` (Coffee) and `/report/43` (FCOJ). I scanned report IDs 1-140 for titles and found no sugar or cotton certified-stock report page. They may exist under other IDs or names. UNVERIFIED.
- The report pages render their data in the browser, so curl and WebFetch show no data, no date picker and no formats. Whether the reports are free, need a login, or hold history: UNVERIFIED. The Report Center itself says "End of day report packages in .csv format are available for purchase on a subscription basis". ICE Fundamentals (a paid data product) covers certified stocks for the four softs.
- Third-party daily summaries (StoneX) exist for coffee (secondary).
- **PIT-safe: UNVERIFIED.** If only the current day is shown, we must snapshot daily ourselves, which gives a PIT archive from the start date only.
- **Verdict: avoid until someone with a browser confirms free access and the report URLs.** Then adopt, with a daily archiver.

## Recommended order

1. **Fix the coffee parser and source (item 2).** It is a live bug: wrong prior figures feed the revision signal, and the source is stale by a year. Anchor the section markers, add the plausibility check, read the date from the PDF, and move to the `apps.fas.usda.gov` path. Remove the cocoa call.
2. **CFTC COT (item 5).** Cheapest new signal; a clean API with release stamps from now on.
3. **Sugar World Markets and Trade (item 3).** Reuse the fixed coffee parser.
4. **Grind data (4c) and ICCO monthly (4b).** Gives cocoa the demand data it currently lacks; ECA, NCA and CAA are simple dated PDFs.
5. **NOAA ENSO discussion (item 6).** Easy, dated prose.
6. **Open-Meteo Historical Forecast (item 1).** Do this after the licence decision; archive our own pulls.
7. **UNICA PDFs and CONAB (item 7).** More work (Portuguese, restatement risk); ingest and keep the PDFs from release.
8. **ICE certified stocks (item 8).** Only after manual confirmation of free access.

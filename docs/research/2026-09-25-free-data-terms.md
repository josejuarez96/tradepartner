# Research Report: Terms and technical facts of Alpaca free market data and SEC EDGAR (T3 support)

**Brief:** plan task T3, issue #84 (orchestrator brief)  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE. Every sub-question has a Tier 1 answer or is marked "not documented". One conflict between two Tier 1 Alpaca pages is still open (see Caveats).  ·  **Agent/model:** research agent, claude-opus-5-5

## Answer
**Verdict:** n/a. This is a factual brief, not a hypothesis, so the grades do not apply. Coverage: 6 of 8 sub-questions are fully answered from Tier 1, and 2 are partly "not documented" (corporate-actions depth, and whether fractional orders join the opening auction).  ·  **Confidence:** medium overall. It is high for the EDGAR facts and for the Alpaca rate limit and plan table, and medium for Alpaca terms and bar construction.

Alpaca's Terms and Conditions limit use to "personal and non-commercial purposes". They forbid copying or redistributing content "for publication or distribution or for any commercial enterprise", but they do not forbid storing data privately or trading your own account algorithmically (S13). The Basic (free) plan gets **real-time** data from IEX only. For **historical** queries, Alpaca's FAQ says SIP data can be queried without a subscription if `end` is at least 15 minutes old (S1). This conflicts with another Alpaca page, which calls IEX "the only feed that can be used without a subscription" (S4). Alpaca builds daily bars from trades. The open is the **first eligible trade**, not the official opening-auction price. The official-open and official-close conditions (Q and M) are explicitly excluded (S1, S20). History is "Since 2016" (S2). The Basic rate limit is 200 requests per minute (S2). Fractional orders support only `day` time-in-force, not OPG or CLS, and are priced off the NBBO (S6, S7). EDGAR allows at most 10 requests per second per user, requires a declared User-Agent of the form "Company Name contact@domain", and publishes nightly `companyfacts.zip` and `submissions.zip` bulk files (S14 to S16).

## Evidence
All pages were seen on 2026-09-25.

| # | Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|---|
| 1a | Use is limited to personal, non-commercial purposes | S13, "Personal and Non-Commercial Usage" | 1 | "you agree to use the Services and Content solely for your own personal and non-commercial purposes" | n/a | n/a |
| 1b | Copying and redistribution restriction, scoped to publication, distribution or commercial use | S13, "Content" | 1 | "No part of the Service or Content may be copied, reproduced, republished, uploaded, posted, publicly displayed … or distributed in any way (including "mirroring") to any other computer, server, web site or other medium for publication or distribution or for any commercial enterprise, without Alpaca's express prior written consent." | n/a | n/a |
| 1c | Basic plan is free. Only the paid plan binds the user to exchange subscriber agreements | S13, "Data Plans" | 1 | "'Basic' market data plan, which is made available at no cost … By selecting the 'Pro' market data plan … you agree to: (i) the NASDAQ OMX Global Subscriber Agreement, or AGREEMENT FOR MARKET DATA DISPLAY SERVICES" | n/a | n/a |
| 1d | Access can be suspended for heavy use | S13, "Termination; Modification" | 1 | "if your usage puts an undue strain on Alpaca's information technology infrastructure" | n/a | n/a |
| 1e | Making the data available to others requires notice | S13 | 1 | "shall provide Alpaca with 30 days advance written notice prior to making such User Application available to others" | n/a | n/a |
| 2a | Free plan: real-time is IEX only | S1, "Market Data Plan Features" | 1 | "Our free market data offering includes live data only from the IEX exchange" | n/a | n/a |
| 2b | Free plan: historical SIP data is allowed if older than 15 minutes | S1 | 1 | "the `end` parameter must be at least 15 minutes old to query SIP data without a subscription" | n/a | n/a |
| 2c | **Conflicts with 2b:** IEX is the only feed without a subscription | S4 (page updated 2026-02-11) | 1 | "iex … The only feed that can be used without a subscription"; IEX is "~2.5% of US market volume" | n/a | n/a |
| 2d | Staff confirm that free-plan historical bars are SIP | S20, Alpaca staff (Dan Whitnable), 2024-05-17 | 2 | "Alpaca uses SIP data for all historical bar calculations … if the `end` datetime includes the most current 15 minutes, the free data plan will return bars based only upon trades executed on the IEX exchange." | n/a | n/a |
| 2e | Daily open and close are trade-based. Official open and close conditions are excluded | S1, bar-aggregation table (page updated 2026-09-21) | 1 | Q "Market Center Official Open" and M "Market Center Official Close": no update to open/close. O (opening trade) and 6 (closing trade): update. I (odd lot), T/U (extended hours): do not update open/close | n/a | n/a |
| 2f | The daily open is the first valid trade, not the auction print. Example of the gap | S20 | 2 | Open is calculated by filtering trade conditions "then simply takes the first trade of the day"; XOM 2024-05-16 bar open 118.54 vs NYSE opening auction 118.89 (≈0.29%) | n/a | n/a |
| 2g | IEX runs its own 9:30 opening match for non-IEX-listed names. This is not the primary exchange's auction | S19, IEX Trading Alert 2017-020 | 1 | "At the start of Regular Market Hours (i.e., 9:30 a.m. ET) … 'Cross Eligible Orders' will participate in the Opening Match." | n/a | n/a |
| 3a | Historical depth, both plans | S2, subscription table | 1 | "Since 2016" | n/a | n/a |
| 3b | Historical depth, support article (Dec 2022) | S17 | 1 | "we currently do not have data further back than 2016"; "a few missing data points towards the beginning" | n/a | n/a |
| 3c | Corporate-actions depth | S10 | 1 | **Not documented.** Also: "Currently Alpaca has no guarantees on the creation time of corporate actions. There may be delays in receiving corporate actions from our data providers." Types include splits, cash_dividend, spin_off, name_change, worthless_removal, among others | n/a | n/a |
| 4 | Rate limits | S2 | 1 | Basic "200 / min"; Algo Trader Plus "10,000 / min"; WebSocket Basic "30 symbols" | n/a | n/a |
| 5a | Fractional orders: allowed time-in-force | S6; S7 fractional table | 1 | "fractional trading for market, limit, stop & stop limit orders with a time in force=Day"; the S7 table shows OPG and CLS unsupported for fractional | n/a | n/a |
| 5b | Fractional orders: price reference | S6 | 1 | "The expected price of fill is the NBBO quote at the time the order was submitted." | n/a | n/a |
| 5c | Orders submitted outside market hours are queued | S7 | 1 | "Orders not eligible for extended hours submitted after 4:00pm ET will be queued up for release the next trading day." Whether a queued DAY order joins the opening auction: **not documented** | n/a | n/a |
| 5d | Paper fills | S8 | 1 | "matched against the best available current market price (NBBO)"; "partial fills for a random size 10% of the time"; quantity "not checked against the NBBO quantities". No auction simulation is documented | n/a | n/a |
| 5e | Fractional orders in live and paper | S6 | 1 | "all Alpaca accounts are allowed to trade fractional shares in both live and paper environments" | n/a | n/a |
| 6a | Assets endpoint: fields and statuses | S11 | 1 | `status` "active or inactive"; "By default, all statuses are included"; fields include id, symbol, cusip, exchange. **No date or listing-history fields** | n/a | n/a |
| 6b | Asset identity across changes | S12 | 1 | A symbol-only change updates the existing asset and keeps the same ID. On a CUSIP change "the current asset becomes inactive" and "a new Asset Object is added" | n/a | n/a |
| 6c | Bars resolve symbols to entities by date | S3, `asof` param | 1 | "This date is used to identify the underlying entity of the provided symbol(s), so that name changes for this entity can be found." Default: current day | n/a | n/a |
| 7a | EDGAR rate limit | S16 (updated 2025-03-10); S14 | 1 | "no more than 10 requests per second, regardless of the number of machines used"; "reserves the right to block IP addresses that submit excessive requests" | n/a | n/a |
| 7b | EDGAR User-Agent | S14 | 1 | `User-Agent: Sample Company Name AdminContact@<sample company domain>.com`; also `Accept-Encoding: gzip, deflate`, `Host: www.sec.gov` | n/a | n/a |
| 7c | Fair-access policy page | S14, S16 | 1 | https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data ; Developer FAQ https://www.sec.gov/os/webmaster-faq#developers | n/a | n/a |
| 8a | Bulk ZIPs | S15 | 1 | `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`, `https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`; "recompiled nightly" at about 3:00 a.m. ET; no auth or API key | n/a | n/a |
| 8b | Submissions JSON scope | S15 | 1 | "at least one year's of filing or to 1,000 (whichever is more)"; older filings in "an array of additional JSON files" | n/a | n/a |
| 8c | Filing indexes | S14 | 1 | Types: company, form, master, XBRL. Fields: company name, form type, CIK, filing date, file name. Daily indexes updated nightly from about 10:00 p.m. ET. Full index is quarterly, from 1994Q3 | n/a | n/a |

## Disconfirmation
- **Searches run:** (a) "Alpaca market data free plan prohibited store data locally algorithmic trading terms"; (b) "Alpaca market data agreement terms non-professional subscriber redistribution"; (c) "Alpaca IEX daily bars differ from SIP open close volume forum" (forum.alpaca.markets and alpaca.markets); (d) IEX opening process for non-IEX-listed securities; (e) Alpaca support "data timeline" to test the depth claim; (f) a fractional-before-open search.
- **Local storage or algorithmic use forbidden?** Nothing found that forbids either. The copying clause (S13) is limited to "for publication or distribution or for any commercial enterprise". The Alpaca product is an API sold for algorithmic trading. The only real constraints are that use must be personal and non-commercial, that access can be suspended under "undue strain", and that the terms can change at any time. S13's PDF metadata dates it 2021-08-31. I could not verify that it is the version currently in force. I did not read the Customer Agreement or any exchange agreement that might add clauses.
- **IEX-only opens differ materially from SIP or official opens?** Found against the naive assumption. (1) Even on SIP, Alpaca's daily open is the first valid trade and not the official auction (S1, S20). In the staff example the gap is 0.29% (XOM, S20). (2) An IEX-feed open is IEX's own 9:30 Opening Match (S19), which is a different venue from the primary auction. The volume difference is large: S4 puts IEX at "~2.5%" of market volume. I found no systematic Tier 1 study measuring the typical gap between IEX opens and primary-auction opens. **Not quantified.**
- **History shorter than claimed?** Partly. Tier 1 says "Since 2016" (S2, S17), which matches ADR 0003's belief. S17 notes "a few missing data points towards the beginning". The exact first date and the completeness of 2016 are not documented. Corporate-actions depth is not documented at all. The pricing page "7+ years" noted in the price-vendors report is consistent with this.

## Caveats & gaps
- **Feed conflict (most important):** S1 and S20 say a free account can get SIP historical bars when `end` is at least 15 minutes old. S4 says only IEX works without a subscription. One API call settles it: `feed=sip`, a past date range, free keys. Until then, treat free-plan SIP history as **probable, not certain**.
- The daily open is never the official opening-auction price on any Alpaca feed (S1 table, S20). A backtest fill at "open" therefore uses a first-trade price, and live fills at the auction or at the NBBO will differ from it.
- Nothing documents a fractional DAY order submitted before the open joining the opening auction. The docs say fractional orders are priced off the NBBO (S6) and that OPG is unsupported for fractional orders (S7), which suggests execution after the open at the NBBO rather than in the cross. This is inference, not documented fact.
- The assets endpoint has no listing history. Symbol reuse by a different company is not directly documented. Because a new company has a new CUSIP, the S12 rule implies a new asset ID for it, but whether the old inactive asset keeps showing the old symbol is not documented.
- Several key Alpaca facts are Tier 2 (the S20 forum answer from staff), marked in the table.

## UNVERIFIED items
- That S13 (TermsAndConditions.pdf, metadata 2021-08-31) is the current version of the terms.
- Whether a free account can actually query `feed=sip` history (see the feed conflict above).
- Exact first date of Alpaca daily bars. How complete 2016 is.
- Alpaca corporate-actions depth. Whether delisted or inactive symbols return bars (the price-vendors report cites forum threads saying they do not).
- EDGAR index file names and extensions (`form.idx`, `master.idx`, `.gz`/`.zip` variants, `form.YYYYMMDD.idx` daily naming). These are from memory and were not quoted from S14.
- How many listed names lack an IEX trade on a normal day. There is no Tier 1 figure.

## Follow-up questions (not answered here)
- Do the Alpaca Customer Agreement or the exchange agreements add market-data clauses for Basic users?
- Delisted-symbol bar coverage on Alpaca (covered in part by the price-vendors report).
- Backfill runtime at 200 requests per minute (named in the spec's "Terms and opens" line, but not asked in this brief).
- The size of the typical gap between the IEX open, the first SIP trade and the primary auction, measured empirically.

## Facts for T3 config (recommendations for the owner, not decisions)
- **`universe.liquidity_rule_enabled`:** IEX carries about 2.5% of US volume (S4), so IEX-only volume is too small and too uneven to use as a liquidity measure. **Recommendation:** keep this `false` if ingest is IEX-only. It could be enabled if a live call confirms that free-plan `feed=sip` historical daily bars work (S1, S20), since SIP volume is consolidated. Alpaca SIP daily volume includes odd lots and extended-hours trades (S1 table), so it is not directly comparable to other vendors' regular-session volume.
- **`execution.fill_price`:** Alpaca's daily open is a first-trade price, not the official auction open, on both feeds (S1, S20; 0.29% in one example). A fractional order cannot use OPG and is priced off the NBBO (S6, S7), and paper fills match against the NBBO (S8). Neither live nor paper fills will equal the bar open. **Recommendation:** keep `close` as the default until the owner records a check of real paper fills against bar opens. If `open` is chosen, pair it with a cost-model slippage allowance. The close has a parallel issue: the official close (M) is excluded and the close is the last valid trade (S1). On SIP this is usually the closing-auction print (condition 6), but that is not guaranteed.
- **`ingest.max_missing_share`:** No Tier 1 source gives how many listed names lack an IEX bar on a normal day. With about 2.5% volume share, many thinly traded names will have no IEX trade on some days. **Recommendation:** if ingest uses SIP historical (run at least 15 minutes after the close, consistent with `settle_delay_minutes`), a small share (low single-digit percent, covering halts and suspensions) is a plausible starting value. If ingest is IEX-only, measure the share empirically on about 20 sessions before setting it, and do not pick a number from this report.

## Owner recording, 2026-09-25 (resolves the open items above)

The owner ran `python -m tradepartner.cli_record` with real keys, then one probe. Facts recorded, with the decision each one drives:

| Fact | Observed | Consequence |
|---|---|---|
| Free plan returns **SIP** history for past dates | `daily_bars(["SPY"], 2024-01-02..05, feed=SIP)` returned four bars with volume 123,007,793 on 2024-01-02: consolidated volume, which IEX (~2.5% of the tape) cannot produce. The `feed: sip` echo alone is the request, not evidence. One symbol, four days; 2016 and delisted-symbol coverage on SIP were not probed (#86) | Caveat "feed conflict" closed in favour of S1/S20. `alpaca.historical_feed` defaults to `sip`; real-time stays IEX. The T3 fixtures hold both recordings (`daily_bars.json` SIP, `daily_bars_iex.json`). |
| Daily open is the first trade, not the auction print (S1, S20), on every feed | Not re-measured; documented behaviour | `execution.fill_price` stays `close`. Revisit in Phase 4 with live fills. |
| Consolidated volume available | From the SIP probe | `universe.liquidity_rule_enabled` set to **`true`**: the 20-session median dollar volume is a real measure. |
| Missing bars on a normal day | Not measurable from a 5-symbol recording | `ingest.max_missing_share` stays `0.05`. On SIP a listed name lacks a bar only on halts and suspensions. `health` reports the observed share; tighten once measured over ~20 sessions (#86). |
| History depth | Documented "since 2016" (S2, S17). The recording window is 2020-08..09 by design (AAPL 4:1 split, SPY/KO dividends), so depth was not probed | Backfill `--since` needs no cap for the phase; ADR 0003's 2016 assumption stands. |
| Corporate-actions depth | Not documented (S10); recording covers one 2020 window, which did return AAPL's split and the dividends | Unknown beyond 2020. Tracked in #86. |
| Assets endpoint ticker reuse | 5 assets, current-only, no listing history (S11); ID stable on symbol change, new ID on CUSIP change (S12) | The master keeps its `snapshot_static` provenance for Alpaca-sourced ticker/exchange; reuse is detected from EDGAR filings, never from assets. |
| Acceptance times | Company facts carry `filed` (date) and `accn` only; the submissions payload's `filings.recent` holds ~1,000 filings with `acceptanceDateTime` (UTC), older ones sit in paged files (`filings.files[]`); the SGML `ACCEPTANCE-DATETIME` is Eastern with no zone. The companyfacts API omits dimensioned facts, so Alphabet's per-class shares appear only in the filing iXBRL | Recorder also records the older pages, trimmed to referenced accessions. Rules for T11 in `tests/fixtures/README.md`: never stamp `known_at` from `filed`; per-class shares from the document. |
| Snapshot fetch time | Git keeps no file times | Recorder writes `tests/fixtures/recorded_at.json` (UTC write time, seconds after fetch, an upper bound); snapshot records use it as `known_at`. |
| EDGAR rate limit and User-Agent | 10 req/s, `Name contact` (S14, S16). The recorder's ~15 calls with the configured throttle completed without a 403 | `edgar` config unchanged. |
| Fixture sizes | Raw company facts 2.4-7.7 MB, companies snapshot 0.9 MB, 10-K documents 1.5-2.6 MB with `dei:` tags spread across the whole file | Recorder now trims facts to `dei` plus share-count concepts, samples the snapshot, and gzips documents (`cli_record.py`). Largest fixture after: 309 KB. |

**Backfill runtime estimate** (documented limits, not measured; assumptions marked):
- The input is **every common listing active at any point since 2016, delisted names included**, not today's top 1,000: picking the top 1,000 by cap at a historical T needs bars for all candidates at that T, or the universe is survivorship-biased. Assume about 8,000 symbols (roughly 4,000 listed at any time plus turnover; assumption).
- Alpaca daily bars: about 8,000 symbols × ~2,700 sessions ≈ 22 M bars at most (most symbols are listed for part of the window). Assuming 10,000 bars per page (S3's `limit` maximum; assumed, verify in T12), about 2,200 requests at 200/min: **about 15 minutes**. Corporate actions: less.
- EDGAR: quarterly full indexes 1994Q3 to date, about 130 files (size unsourced; tens of MB each is plausible) at 10 req/s: **minutes to an hour**, bandwidth-bound. Submissions plus company facts for the roughly 10,000 issuers that ever had a listing in the window (assumption): about 20,000 requests at 10/s: **about 35 minutes**, or one download each of `submissions.zip` and `companyfacts.zip` (nightly rebuilds), which is the better path for the initial backfill.
- Total: still **inside one overnight run** with margin, so the plan's fallback (`--since` defaulting to three years) is not needed. Record the measured runtime in T17.

**Terms conclusion for ADR 0003:** personal, non-commercial use with local storage is allowed (S13). Nothing in the free plan forbids algorithmic trading of the owner's own account. The paid plan, not the free one, binds the user to exchange subscriber agreements.

## Sources
All were seen on 2026-09-25.
- **S1** Alpaca, Market Data FAQ (updated 2026-09-21): https://docs.alpaca.markets/us/docs/market-data-faq (Tier 1)
- **S2** Alpaca, About Market Data API (subscription plans): https://docs.alpaca.markets/us/docs/about-market-data-api (Tier 1)
- **S3** Alpaca, Historical bars reference: https://docs.alpaca.markets/us/reference/stockbars (Tier 1)
- **S4** Alpaca, Historical Stock Data (updated 2026-02-11): https://docs.alpaca.markets/us/docs/historical-stock-data-1 (Tier 1)
- **S5** Alpaca, docs index: https://docs.alpaca.markets/us/llms.txt (Tier 1, navigation only)
- **S6** Alpaca, Fractional Trading: https://docs.alpaca.markets/us/docs/fractional-trading (Tier 1)
- **S7** Alpaca, Placing Orders (time in force): https://docs.alpaca.markets/us/docs/orders-at-alpaca (Tier 1)
- **S8** Alpaca, Paper Trading: https://docs.alpaca.markets/us/docs/paper-trading (Tier 1)
- **S9** Alpaca, Working with Assets: https://docs.alpaca.markets/us/docs/working-with-assets (Tier 1, nothing relevant found)
- **S10** Alpaca, Corporate Actions reference: https://docs.alpaca.markets/us/reference/corporateactions-1 (Tier 1)
- **S11** Alpaca, Get Assets reference: https://docs.alpaca.markets/us/reference/get-v2-assets-1 (Tier 1)
- **S12** Alpaca, Mandatory Corporate Actions: https://docs.alpaca.markets/us/docs/mandatory-corporate-actions (Tier 1)
- **S13** Alpaca Terms and Conditions (PDF, metadata 2021-08-31): https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf (Tier 1)
- **S14** SEC, Accessing EDGAR Data: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data (Tier 1)
- **S15** SEC, EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces (Tier 1)
- **S16** SEC, Developer Resources (updated 2025-03-10): https://www.sec.gov/about/developer-resources (Tier 1)
- **S17** Alpaca Support, data timeline (Dec 2022): https://alpaca.markets/support/alpaca-data-timeline (Tier 1)
- **S18** IEX, Auctions: https://iextrading.com/trading/auctions (search snippet only, not relied on)
- **S19** IEX Trading Alert 2017-020, Opening Process for Non-IEX-Listed Securities: https://iextrading.com/trading/alerts/2017/020/ (Tier 1)
- **S20** Alpaca Community Forum, staff answer 2024-05-17: https://forum.alpaca.markets/t/open-close-daily-bar-prices-vs-open-close-auction-prices-on-primary-exchange/14227 (Tier 2)

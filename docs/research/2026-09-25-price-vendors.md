# Research Report: Survivorship-bias-free US daily price vendors for an individual (G8)

**Brief:** #46  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE. Three passes are done and all budgets are spent. Still open from the pass 3 targets: EODHD's post-termination deletion clause (conflicting sources) and Alpaca's exact daily-history start date. See "UNVERIFIED items".  ·  **Agent/model:** research agent, team banshee, claude-opus-5-5

All prices were retrieved on **2026-09-25**. Vendor prices change, so re-check them before the ADR.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium on prices and plan terms; low on how complete the delisted coverage really is

Four vendors state in their own docs or pricing pages that an individual can buy US daily prices that include delisted names:

- **Norgate:** Platinum is USD 630/yr with delisted names back to 1990. Diamond is USD 787.50/yr, back to 1950. The data app is Windows-only.
- **Sharadar SEP:** the Prices plan is $39/mo for full history (history from 1998). A 5-year plan costs $9/mo.
- **EODHD:** $199/yr includes "Delisted Data". Names delisted before 2018 have EOD prices only.
- **Massive (formerly Polygon):** "delisted tickers keep their full history". The 20+ year plan costs $199/mo.

**Tiingo** ($300/yr) supports delisted tickers "that have not yet been recycled", so its coverage is partial by its own account. **Alpaca**'s docs say nothing about delisted names. **CRSP** is licensed only to institutions.

Nobody has independently **verified** any vendor's delisted coverage. No vendor records when a corporate action became known. Every adjusted series examined is restated backwards: Sharadar, EODHD and Massive say so explicitly, and Norgate is not documented. Every licence I read is for personal use only and forbids redistribution. Norgate, Tiingo, Sharadar (direct and via QuantRocket) and Massive all require you to delete local copies when the subscription ends. Sharadar alone explicitly lets you keep derived outputs (backtest results, models, statistics) that "cannot reproduce the Services Data". EODHD's post-termination terms are unresolved: its fetched T&C page is silent, while a search snippet says delete "within one (1) month".

## Evidence

### Comparison table (one citation per cell; `S#` refers to Sources; "nv" = not verified within budget)

| Vendor | Delisted coverage (grade) | Adjustment method | Point-in-time corporate actions | History depth | Price (retrieved 2026-09-25) | Licence / local storage | API / bulk |
|---|---|---|---|---|---|---|---|
| **Norgate** | **Claimed.** Platinum: "Delisted Securities Back to 1990". Diamond: "Back to 1950" [S1]. US coverage "essentially complete back to late 1992"; delisted symbols get a "-YYYYMM" suffix [S2] | Four modes: "Unadjusted (raw), Adjusted for Capital Reconstructions, … and Special Distributions, … Special Distributions and Normal Distributions - aka Total Return" [S2] | nv. No known-at date for actions is documented [S2] | Platinum "Back to 1990"; Diamond "Back to 1950" [S1] | Platinum $346.50/6 mo, **$630/12 mo**. Diamond $433.13/6 mo, **$787.50/12 mo** [S1]. US Stocks packages are "priced in US Dollars" [S20]. Gold and Silver have no delisted names [S1] | Personal use only: "our data service can only be licensed for personal use by individuals. There is no alternative business/commercial licensing" [S14]. Rights "may not be transferred to another party" (cl. 3(i)) [S19]. "Following any expiration of a Subscription the Licensee must delete all Content and Information related to that subscription" (cl. 21) [S19]. Backups "must be deleted" [S14]. Two machines; a VM counts as one [S14]. Australian law (cl. 23) [S19] | Python package [S2]. **Windows only:** "NDU will only work under Windows. However, you can run NDU indirectly on a Mac via Windows virtualization software … UTM, VMWare Fusion …, VirtualBox …, Parallels Desktop" [S21]. Requires ".NET Framework v.4.8" [S18]. Planned move to .NET10 "in late 2026 or early 2027", Windows 10+ only [S21] |
| **Sharadar (SEP)** | **Claimed.** "No survivorship bias: includes active and delisted tickers" [S3] | closeadj adjusted "for stock splits and cash dividends and spinoffs"; closeunadj is not [S16] | **No.** "Adjustments are applied on a backwards basis. This means today's adjusted price will always equal the price traded in the market" [S16]. The "point-in-time ready" wording from pass 1 was **dropped**: it did not appear on any fetched page | Prices "1998 - present" [S3]. Plans come in 5 years, 10 years or Full History [S22] | Direct from Sharadar: **Prices "$9/mo" (5 Years) or "$39/mo" (Full History)**. Bundle $29/mo (5 yrs) or "$69/month ($499/year)" full history. "Annual options available"; Prices annual figure nv [S22]. Nasdaq Data Link price: nv | Direct: "This License is granted solely to natural persons for personal use." "Within thirty (30) days of termination, delete from all computer systems you own or operate all copies of the Services Data (including downloads, bulk files, caches, and extracts)." You may keep "research outputs, backtest results, models, summary statistics, trade logs, and similar derived works that do not contain and cannot reproduce the Services Data". You "may not publish, disseminate, re-distribute or share the Services Data" [S25]. Via QuantRocket: no redistribution, and delete "within thirty (30) days of termination" [S3]. Professional users "must purchase Sharadar data from Nasdaq Data Link" [S3]. Direct Nasdaq Data Link terms: nv | API and "Bulk downloads" [S22] |
| **CRSP** | Delisted coverage **not evaluated**; not sold to individuals: "delivered to licensees at academic institutions, government agencies, and investment practitioners" [S4] | nv | nv | "Over 100 Years" [S4] | Not listed [S4] | Institutional only [S4] | "Flat File Format 2.0 (CIZ)" [S4] |
| **Tiingo** | **Claimed, partial by the vendor's own statement.** "supports delisted data for tickers that have not yet been recycled". PermaTicker and delisted support are still future work [S5] | Provides both unadjusted and adjusted (adjOpen/High/Low/Close/Volume) prices, following "the standard method set forth by 'The Center for Research in Security Prices' (CRSP)". divCash is dated on the "exDate"; splitFactor is "the factor used to adjust prices when a company splits, reverse splits, or pays a distribution" [S17] | Ex-date only [S17]. No known-at date documented | "30+ Years" [S6]. Start date per ticker is "the earliest date we have price data available" [S17] | Power "$30/month" or "$300/year"; Business "$50/month (or $499/year)" [S6] | "Internal Use Only" [S6]. On paid plans: "you may persist Tiingo Data in storage solely to the extent permitted by that Paid Plan". "Upon the expiration, cancellation, or termination of the Paid Plan…you must promptly and permanently delete all Tiingo Data from every system". Starter (free) plan: "you may not write, save, archive, back up, or otherwise retain Tiingo Data in any persistent or durable storage". Redistribution "only available upon special request and permission, and comes with additional fees" [S24] | REST API, 10,000 requests/hour on Power [S6]. supported_tickers.zip is a daily **ticker list**, not a price bulk file [S17]. No price bulk download documented |
| **Massive (formerly Polygon.io)** | **Claimed.** "delisted tickers keep their full history, so backtests see the market as it actually was" [S13] | Aggregate bars split-adjusted by default; `adjusted=false` gives raw bars. No dividend adjustment. The vendor advises "Store the unadjusted prices if you are keeping a permanent record" [S8] | **No.** Adjusted series are recomputed retroactively [S8]. Corporate actions: "splits, dividends, and IPOs — back to 2008" [S13] | Starter 5 yrs, Developer 10 yrs, Advanced "20+ years" [S7]. Tick data "since 2003" [S13] | Starter $29/mo; Developer $79/mo; **Advanced $199/mo**; annual billing saves 20% [S7] | "Individual use", "Non-pros only" [S7]. Use is limited to "your personal, non-business, and non-commercial purposes". On termination: "cease all use of the Market Data and delete all Market Data in your possession". Market Data "may not be copied, reproduced, republished, … transmitted, or distributed in any way". The terms are silent on local storage during the subscription [S26] | REST API. S3 flat files on Starter, Developer, Advanced and Business; **not** on the free tier [S13] |
| **EODHD** | **Claimed, tiered.** "Delisted Data" is included in both the $19.99 and $99.99 plans [S9]. Names delisted "After 2018" have EOD, fundamentals, dividends and splits; "Before 2018": EOD only. The vendor says to "contact our support team" to confirm coverage per ticker [S10]. The "26,000+ delisted US tickers" figure from pass 1 was **dropped**: it was not on any fetched page | adjusted_close: "Closing price adjusted for both splits and dividends". "The OHLC fields are **raw**" [S15] | **No.** "Adjusted closes are recomputed, not stored. Every new dividend re-scales the whole history behind it" [S15] | "Major US Companies: from 1985" [S9]. "The oldest US common stocks start January 2, 1962" [S15] | EOD All World **$19.99/mo, $199.00/yr**; All-In-One $99.99/mo, $999.90/yr [S9] | "Personal use"; commercial use needs a separate plan [S9]. "Non-Professional Users are permitted to store, manipulate, and analyze the data for private, non-commercial purposes". Prohibits "Selling, reselling, retransmitting, redistributing, displaying, or granting access to the Information" [S27]. Post-termination deletion: **nv**. The fetched T&C [S27] and license page [S28] show no clause. A search snippet of the T&C says delete all copies "within one (1) month" of termination (UNVERIFIED) | 100,000 calls/day [S9]. Bulk API is included in "All-In-One, EOD Historical Data — All World, and EOD+Intraday — All World Extended"; "Bulk downloads are not part of the Free plan". "Each request consumes 100 API calls per entire exchange"; one trading date per request, reaching back decades (e.g. 2 January 1990: 2,197 US rows) [S29] |
| **Alpaca** | **Absent (not documented).** *Superseded 2026-09-25: observed for a sample of 8 delisted names, SIP bars to the last session ([#104 probe](2026-09-25-alpaca-delisted-bars.md)).* The docs say nothing about delisted names [S11, S12]. The FAQ only says "Make sure the asset is active. Check the `status` field" [S23] | `adjustment`: "raw" (default), "split", "dividend", "spin-off", "all" [S12] | Partial: `asof` maps symbols across renames [S12]. No known-at date for actions | "7+ years" on both Free and Algo Trader Plus [S30]. Exact start date: **nv** (a support-page snippet says data does not go "further back than 2016"; UNVERIFIED) | **Free $0/mo** (IEX feed, 200 API calls/min); **Algo Trader Plus $99/mo** ("All US Exchanges", unlimited calls) [S30]. Free plan: "to query any SIP trades or quotes in the last 15 minutes, you need the Algo Trader Plus subscription" [S23] | nv (not a pass 3 target) | REST API; feeds sip, iex, boats, otc [S12] |

### Evidence rows (template format)

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Norgate delisted names only in Platinum and Diamond | S1 | 1 | "Delisted Securities Back to 1990" / "Back to 1950"; $630 / $787.50 per 12 months | n/a | n/a |
| Norgate US packages priced in USD | S20 | 1 | "priced in US Dollars" | n/a | n/a |
| Norgate delisted coverage is complete from late 1992 | S2 | 1 | "essentially complete back to late 1992" | n/a | n/a |
| Norgate requires deletion when the subscription expires | S19, S14 | 1 | "must delete all Content and Information related to that subscription" (cl. 21) | n/a | n/a |
| Norgate data app is Windows-only | S21 | 1 | "NDU will only work under Windows" | n/a | n/a |
| Sharadar direct pricing | S22 | 1 | Prices "$9/mo" (5 Years) / "$39/mo" (Full History) | n/a | n/a |
| Sharadar adjustment is backward and restated | S16 | 1 | "Adjustments are applied on a backwards basis" | n/a | n/a |
| Sharadar includes delisted names; reseller licence requires deletion | S3 | 1 (reseller) | "includes active and delisted tickers"; delete "within thirty (30) days" | n/a | n/a |
| CRSP not sold to individuals | S4 | 1 | "delivered to licensees at academic institutions, government agencies, and investment practitioners" | n/a | n/a |
| Tiingo delisted coverage excludes recycled tickers | S5 | 1 | "not yet been recycled" | n/a | n/a |
| Tiingo adjusts by the CRSP method | S17 | 1 | "the standard method set forth by … (CRSP)" | n/a | n/a |
| Tiingo requires deletion on cancellation | S24 | 1 | "must promptly and permanently delete all Tiingo Data from every system" | n/a | n/a |
| Massive claims no survivorship bias; corporate actions from 2008 | S13 | 1 (vendor product page) | "delisted tickers keep their full history"; "back to 2008" | n/a | n/a |
| Massive: no dividend adjustment; adjusted series restated | S8 | 1 | "Store the unadjusted prices if you are keeping a permanent record" | n/a | n/a |
| EODHD adjusted_close is recomputed | S15 | 1 | "Adjusted closes are recomputed, not stored" | n/a | n/a |
| EODHD pre-2018 delisted names are EOD only | S10 | 1 | "Before 2018": EOD only | n/a | n/a |
| Alpaca free plan restricts recent SIP data | S23 | 1 | "last 15 minutes … need the Algo Trader Plus subscription" | n/a | n/a |
| Sharadar direct licence: delete at termination, derived works may be kept | S25 | 1 (ToS) | "Within thirty (30) days of termination, delete … all copies of the Services Data" | n/a | n/a |
| Massive requires deletion at termination | S26 | 1 (ToS) | "delete all Market Data in your possession" | n/a | n/a |
| EODHD non-professional use, no redistribution | S27 | 1 (ToS) | "permitted to store, manipulate, and analyze the data for private, non-commercial purposes" | n/a | n/a |
| EODHD Bulk API plan coverage | S29 | 1 | "Bulk downloads are not part of the Free plan" | n/a | n/a |
| Alpaca Algo Trader Plus price and history | S30 | 1 (pricing page) | "$99/mo"; "7+ years" | n/a | n/a |

The template's OOS and costs columns do not apply to a vendor survey. On tiers: handoff §6.1 counts vendor *marketing* as Tier 3, while this brief counts vendor docs, ToS and pricing pages as Tier 1. Survivorship statements therefore stay at **claimed**, because their only source is the vendor.

### Grade definitions used for delisting coverage
- **Verified:** the vendor documents delisted coverage, **and** an independent Tier 1 source or our own data test confirms it. No vendor reached this grade.
- **Claimed:** the vendor documents or states delisted coverage, with no independent confirmation.
- **Absent:** the vendor does not document delisted coverage, or documents that it lacks it.

Pass 2 changed only one grade basis: Massive is still **claimed**, but the claim is now cited from a fetched page (S13) instead of a search snippet.

## Disconfirmation
- **Searches run in pass 1** (7):
  - Norgate complaints.
  - Sharadar errors.
  - Tiingo delisted and recycled tickers.
  - Polygon data quality.
  - EODHD data quality.
  - Alpaca delisted symbols.
  - Norgate licence retention.

  CRSP was not searched because it is not available to individuals. Pass 2 ran no new disconfirmation searches; its budget went to closing unverified cells.
- **What was found against.** The user reports below are Tier 3 flags to verify; none was fetched. The vendor-doc items are marked as such.
  - **Norgate:** no user complaints found. Vendor-doc risks: the data app is Windows-only, so on a Mac it needs a VM, which counts toward the two-machine limit. A platform move to .NET10 "in late 2026 or early 2027" will need Windows 10+ [S21]. Licensed data must be deleted at expiry [S19].
  - **Sharadar:** a GitHub issue ([flabber1835/stocker #237](https://github.com/flabber1835/stocker/issues/237)) reports a stale cash-distribution value shared by ACTIONS and the SEP total-return adjustment, "a common-mode vendor error that internal Sharadar corroboration cannot detect". Also, pass 1's "point-in-time ready" wording could not be found on a fetched page and was dropped.
  - **Tiingo:** the vendor's own doc limits delisted coverage to non-recycled tickers [S5]. A Tier 3 README snippet describes recycled tickers silently resolving to the wrong company.
  - **Massive/Polygon:** GitHub issues [polygon-io/issues #311](https://github.com/polygon-io/issues/issues/311) (incorrect splits) and [#111](https://github.com/polygon-io/issues/issues/111) (missing or bad splits; an erroneous CPRT split on 2017-04-10). A [Medium review](https://medium.com/@yolotrading/a-complete-review-of-the-polygon-io-api-everything-you-wanted-to-know-c79e992a74ff) says Polygon is "not recommended if you need data on delisted tickers" and reports missing SPY dividends from 2020. Together these contradict the vendor's S13 claim. The dates of the reports are unknown.
  - **EODHD:** no user reports found. The vendor limits pre-2018 delisted names to EOD only and asks users to confirm coverage per ticker with support [S10]. The "26,000+" figure is not on its pages.
  - **Alpaca:** forum threads (["Get Historical Data for Inactive Stocks"](https://forum.alpaca.markets/t/get-historical-data-for-inactive-stocks/10097), ["Delisted tickers"](https://forum.alpaca.markets/t/delisted-tickers/18227)) report that inactive symbols return no bars. *Contradicted for SIP daily bars by the [#104 probe](2026-09-25-alpaca-delisted-bars.md).*

## Caveats & gaps
- **No independent verification.** Every "claimed" grade rests on the vendor's word. Only a data test can move a vendor to "verified".
- **Adjusted prices are not point-in-time.** Sharadar [S16], EODHD [S15] and Massive [S8] say explicitly that adjusted series are restated backwards. Tiingo documents ex-dates only [S17]. No vendor documents announcement or known-at timestamps.
- **Norgate currency resolved.** US packages are priced in USD [S20]. Pass 1's AUD snippet matches the Australian packages, which are priced in AUD (search snippet).
- **Depth differs by coverage type.** EODHD's delisted names before 2018 have EOD only [S10]. Norgate's delisted coverage is "essentially complete" from late 1992 [S2]. Sharadar starts in 1998 [S3]. Massive's corporate actions start in 2008 [S13]. Sharadar's $9/mo plan is 5 years only [S22].
- **Licences end with deletion.** Norgate [S19], Tiingo [S24], Sharadar direct [S25] and via QuantRocket [S3], and Massive [S26] all require deleting local data after the subscription ends.
  - Sharadar's direct licence is the only one that explicitly lets you keep derived works that "cannot reproduce the Services Data" [S25].
  - Tiingo's free Starter plan forbids any persistent storage [S24].
  - EODHD's post-termination clause is unresolved (see UNVERIFIED items).
- Fetch attempts that returned nothing usable, not counted as sources:
  - Pass 1 (5): data.nasdaq.com/databases/SEP, sharadar.com, the Sharadar datasheet PDF, and two crsp.org redirects.
  - Pass 2 (1): tiingo.com/about/terms returned 404.

## UNVERIFIED items
Still open after pass 3:
- **EODHD:** the post-termination deletion clause. A search snippet of eodhd.com/financial-apis/terms-conditions says delete all copies "within one (1) month" and that EODHD may "request confirmation of deletion". The fetched page [S27] showed no such clause, and neither did the license page [S28]. Treat this as unresolved; it needs a manual read of the page.
- **Alpaca:** exact start date for daily bars. *Resolved 2026-09-25: first bar 2016-01-04 for SPY and KO ([#104 probe](2026-09-25-alpaca-delisted-bars.md)).* The pricing page gives only "7+ years" [S30]. A support-page snippet ("not further back than 2016") was not fetched. Alpaca's licence and storage terms (not a pass 3 target).
- **Sharadar:** the Prices plan annual price (not on the terms page; the pricing page says only "Annual options available"). Nasdaq Data Link price and licence terms (target 5; no budget left).
- **Massive:** whether local storage is permitted *during* the subscription. The Market Data Terms are silent on this [S26].
- **Norgate:** whether the adjusted series are restated backwards (not documented on the fetched pages); an explicit redistribution clause (cl. 3(i) covers transfer of licence rights only).
- **CRSP:** delisted coverage and adjustment method (low priority; not sold to individuals).

## Follow-up questions (not answered here)
1. Is running Norgate's Windows-only updater in a VM on the owner's Mac acceptable? Does its planned .NET10 move change that?
2. A test for each shortlisted vendor: take N Form 25 delistings from the local EDGAR store and check that bars exist up to the final session. This is the only route to a "verified" grade.
3. Can corporate-action known-at timestamps be sourced separately (e.g. 8-K filings), given that no vendor provides them?
4. Every licence read requires deleting vendor data at cancellation: Norgate, Tiingo, Sharadar and Massive, and EODHD probably. So what may the Parquet export and tagged backtest artefacts (ADR 0003) contain? Only Sharadar's direct licence [S25] explicitly allows keeping derived works (backtest results, models, statistics, trade logs) that "cannot reproduce the Services Data". For the other vendors, whether derived artefacts survive cancellation is not stated in what was read.
5. Does Massive's delisted coverage survive a sample test, given the Tier 3 reports against it?

## Sources
Retrieved 2026-09-25. Tier per the brief's source rules (vendor docs, ToS and pricing pages count as Tier 1).

Pass 1:
- **S1** Norgate, US Stock Market Packages: https://norgatedata.com/stockmarketpackages.php (Tier 1)
- **S2** Norgate, Data Package FAQ: https://norgatedata.com/data-package-faq.php (Tier 1)
- **S3** QuantRocket, Sharadar Data Pricing (authorised reseller; licence terms): https://www.quantrocket.com/pricing/data/sharadar/ (Tier 1, reseller)
- **S4** Morningstar Indexes / CRSP, CRSP US Stock Databases: https://indexes.morningstar.com/research-data-products/crsp-us-stock-databases (Tier 1)
- **S5** Tiingo, Symbology documentation: https://www.tiingo.com/documentation/appendix/symbology (Tier 1)
- **S6** Tiingo, Pricing: https://www.tiingo.com/about/pricing (Tier 1)
- **S7** Massive, Pricing: https://massive.com/pricing (Tier 1)
- **S8** Massive KB, "Is Massive's stock data adjusted for splits or dividends?": https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends (Tier 1)
- **S9** EODHD, Pricing: https://eodhd.com/pricing (Tier 1)
- **S10** EODHD, Delisted Stock Companies Data: https://eodhd.com/financial-apis/delisted-stock-companies-data-2 (Tier 1)
- **S11** Alpaca, Historical Stock Data: https://docs.alpaca.markets/us/docs/historical-stock-data-1 (Tier 1)
- **S12** Alpaca, Historical bars API reference: https://docs.alpaca.markets/us/reference/stockbars (Tier 1)

Pass 2:
- **S13** Massive, Stock Market API product page: https://massive.com/stocks (Tier 1, vendor product page)
- **S14** Norgate, Subscription & Licensing FAQ: https://norgatedata.com/faq.php (Tier 1)
- **S15** EODHD, End-of-Day Historical Data API docs: https://eodhd.com/financial-apis/api-for-historical-data-and-volumes (Tier 1)
- **S16** Sharadar, "Sharadar Stock Prices, Fund Prices and Adjustments" (2026-07-29): https://sharadar.com/blog/posts/sharadar-stock-prices-fund-prices-and-adjustments (Tier 1, vendor doc)
- **S17** Tiingo, End-of-Day API documentation: https://www.tiingo.com/documentation/end-of-day (Tier 1)
- **S18** Norgate, System Requirements: https://norgatedata.com/system-requirements.php (Tier 1)
- **S19** Norgate, End User Licence Agreement: https://norgatedata.com/subscribe/eula.php (Tier 1)
- **S20** Norgate, Subscription prices: https://norgatedata.com/prices.php (Tier 1)
- **S21** Norgate, Norgate Data Updater FAQ: https://norgatedata.com/ndu-faq.php (Tier 1)
- **S22** Sharadar, Subscribe: https://sharadar.com/subscribe (Tier 1)
- **S23** Alpaca, Market Data FAQ: https://docs.alpaca.markets/us/docs/market-data-faq (Tier 1)
- **S24** Tiingo, Terms of Use: https://app.tiingo.com/tos/ (Tier 1)

Pass 3:
- **S25** Sharadar, Terms of Use: https://sharadar.com/terms (Tier 1)
- **S26** Massive, Market Data Terms of Service: https://massive.com/legal/market-data-terms-of-service (Tier 1)
- **S27** EODHD, Terms and Conditions: https://eodhd.com/financial-apis/terms-conditions (Tier 1)
- **S28** EODHD, Commercial vs Personal license use: https://eodhd.com/financial-apis/commercial-vs-personal-license-use (Tier 1)
- **S29** EODHD, Bulk API for EOD, Splits and Dividends: https://eodhd.com/financial-apis/bulk-api-eod-splits-dividends (Tier 1)
- **S30** Alpaca, Market Data plans/pricing: https://alpaca.markets/data (Tier 1)

Search-snippet references (not fetched, flags only, UNVERIFIED):
- The EODHD "within one (1) month" deletion clause.
- Alpaca support: "Does Alpaca Data API have data prior to 2016?" (https://alpaca.markets/support/alpaca-data-timeline).
- The Norgate AUD pricing for Australian packages.
- The Tier 3 links in Disconfirmation.

## Pass 2 note
The owner authorised a second budget of 12 sources and 20 searches on 2026-09-25.

**Used in pass 2:** 12 of 12 sources (S13–S24); 6 of 20 searches; 1 further fetch returned 404.

**Cumulative:** 24 sources; 23 searches.

**Closed in pass 2:**
- Massive: survivorship claim, corporate-actions depth, which plans include flat files.
- Norgate: FAQ deletion clause, billing currency, macOS/OS requirements, EULA transfer and deletion clauses.
- EODHD: adjusted_close definition; its Bulk API exists.
- Sharadar: closeadj definition, direct price, bulk downloads.
- Tiingo: adjusted fields, storage and deletion terms, redistribution, bulk (ticker list only).
- Alpaca: free-plan SIP restriction.

**Dropped as unsupported:**
- The Sharadar "point-in-time ready" wording.
- The EODHD "26,000+ delisted US tickers" figure.

## Pass 3 note
The owner approved a short pass (6 sources, 8 searches) limited to licence-terms and pricing pages.

**Used in pass 3:** 6 of 6 sources (S25–S30); 5 of 8 searches.

**Cumulative:** 30 sources; 28 searches.

**Closed in pass 3:**
- Sharadar direct licence: deletion within 30 days, derived works may be kept, no redistribution.
- Massive: deletion at termination, no redistribution.
- EODHD: redistribution prohibition and non-professional storage permission; Bulk API plans (EOD All World, All-In-One, EOD+Intraday; not Free).
- Alpaca: Algo Trader Plus $99/mo; "7+ years" of history.

**Not closed:**
- EODHD post-termination deletion (fetched page and snippet disagree).
- Alpaca exact start date.
- Sharadar Prices annual price.
- Nasdaq Data Link (target 5; skipped for budget).

No delisted-coverage grade changed.

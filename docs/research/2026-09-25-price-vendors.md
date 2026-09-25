# Research Report: Survivorship-bias-free US daily price vendors for an individual (G8)

**Brief:** #46  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE (source budget of 12 spent; see "UNVERIFIED items")  ·  **Agent/model:** research agent, team banshee, claude-opus-5-5

All prices were retrieved on **2026-09-25**. Vendor prices change, so re-check them before the ADR.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium on prices and plan terms; low on how complete the delisted coverage really is

Four vendors say in their own docs or pricing pages that an individual can buy US daily prices that include delisted names. **Norgate** Platinum costs $630/yr, with delisted names back to 1990; Diamond costs $787.50/yr and goes back to 1950. **EODHD** costs $199/yr with "Delisted Data" included, but for names delisted before 2018 it has EOD prices only. **Massive (formerly Polygon)** says its data is free of survivorship bias, but only in a search snippet I did not fetch; its 20+ year plan costs $199/month. **Sharadar** SEP covers 1998 onward, but I could not see its price. **Tiingo** ($300/yr) supports delisted tickers "that have not yet been recycled", so its coverage is partial by its own description. **Alpaca**'s docs say nothing about delisted names, and forum reports say inactive symbols return no bars. **CRSP** is licensed only to academic, government and practitioner institutions, not individuals.

No vendor's delisted coverage is independently **verified**: every "yes" is the vendor's own claim. None of the vendors documents when a corporate action became known. Their adjusted series are recomputed backwards, so they are not point-in-time. Every paid licence I could read limits use to personal or internal use. The two licences with readable post-cancellation terms (Norgate and Sharadar via QuantRocket) require you to delete local copies when the subscription ends.

## Evidence

### Comparison table (one citation per cell; `S#` refers to Sources; "nv" = not verified within budget)

| Vendor | Delisted coverage (grade) | Adjustment method | Point-in-time corporate actions | History depth | Price (retrieved 2026-09-25) | Licence / local storage | API / bulk |
|---|---|---|---|---|---|---|---|
| **Norgate** | **Claimed.** Platinum and Diamond: "Delisted Securities Back to 1990" (Platinum) / "Back to 1950" (Diamond) [S1]. US delisted coverage "essentially complete back to late 1992"; delisted symbols carry a "-YYYYMM" suffix [S2] | Four modes: "Unadjusted (raw), Adjusted for Capital Reconstructions, … and Special Distributions, … Special Distributions and Normal Distributions - aka Total Return" [S2] | nv. No known-at date for actions is documented [S2 silent] | Platinum "Back to 1990"; Diamond "Back to 1950" [S1] | Platinum $346.50/6 mo, **$630/12 mo**; Diamond $433.13/6 mo, **$787.50/12 mo**. Gold ($360/yr, 20 yrs) and Silver ($270/yr, 10 yrs) have **no** delisted names [S1]. Currency conflict: the fetched page reads USD, but a search snippet read "AUD 346.50 / AUD 630" (UNVERIFIED) | Exported data and backups must be deleted after the subscription expires; no exceptions [search snippet of norgatedata.com/faq.php, not fetched: **UNVERIFIED**]. Redistribution terms: nv | Python package on PyPI [S2]. OS requirements: nv (see Follow-up questions) |
| **Sharadar (SEP) / Nasdaq Data Link** | **Claimed.** "No survivorship bias: includes active and delisted tickers" [S3]. Search snippet: ">21,000 active and delisted tickers … history to the year 1998" (UNVERIFIED) | Search snippet of Sharadar's blog: closeadj is "adjusted for stock splits, stock dividends, cash dividends and spinoffs" (**UNVERIFIED**, not fetched). There is also a separate ACTIONS table (snippet) | Marketing snippet says "point-in-time ready" (**UNVERIFIED**, Tier 3 wording) | Prices "1998 - present" [S3] | **nv.** QuantRocket shows prices only after login [S3]. The Nasdaq Data Link page did not render | Via QuantRocket: no redistribution, and "within thirty (30) days of termination, you will delete from all computer systems…all copies of the Services Data" [S3]. Professional users "must purchase Sharadar data from Nasdaq Data Link" [S3]. Direct Nasdaq Data Link terms: nv | nv (Nasdaq Data Link page did not render) |
| **CRSP** | Delisted coverage **not evaluated**. The product is not sold to individuals: "designed for and delivered to licensees at academic institutions, government agencies, and investment practitioners" [S4] | nv [S4 silent] | nv | "Over 100 Years of Research Quality Data" [S4] | Not listed; "Request Subscription Information" [S4] | Institutional licence only [S4] | "Flat File Format 2.0 (CIZ)" [S4] |
| **Tiingo** | **Claimed, partial by the vendor's own statement.** "Tiingo supports delisted data for tickers that have not yet been recycled." PermaTicker and delisted support are still future work: the guide "will be expanded as we ready to expand our API into permatickers and delisted ticker support" [S5] | Search snippet of Tiingo EOD docs: adjOpen/adjHigh/adjLow/adjClose/adjVolume, divCash, splitFactor on the ex-date (**UNVERIFIED**, not fetched) | nv | "30+ Years" [S6] | Power "$30/month" or "$300/year"; Business "$50/month (or $499/year)" [S6] | "Internal Use Only: you may only use the data for your own personal use and you may not display or share the data with another person or organization" [S6]. Storage after cancellation: nv | REST API with 10,000 requests/hour on Power [S6]. Search snippet: daily supported_tickers.zip (UNVERIFIED) |
| **Massive (formerly Polygon.io)** | **Claimed (snippet only).** Search snippet of massive.com/stocks: "free of survivorship bias — delisted tickers keep their full history" (**UNVERIFIED**, not fetched) | Aggregate bars are split-adjusted by default; "adjusted=false" gives raw bars. Nothing is dividend-adjusted. The vendor advises "Store the unadjusted prices if you are keeping a permanent record" because adjusted series are recomputed retroactively [S8] | **No.** Adjusted series change retroactively with each new split [S8]. Search snippet: corporate actions "dating back to 2008" (UNVERIFIED) | Starter 5 yrs, Developer 10 yrs, Advanced "20+ years" [S7] | Starter $29/mo; Developer $79/mo; **Advanced $199/mo** (20+ yrs); annual billing saves 20% [S7] | Plans marked "Individual use" and "Non-pros only" [S7]. Storage and redistribution terms: nv | REST API. Search snippet: S3 flat files (UNVERIFIED which plans include them) |
| **EODHD** | **Claimed, tiered.** "Delisted Data" is included in both the $19.99 and $99.99 plans [S9]. Depth depends on when the name delisted: "After 2018" = EOD, fundamentals, dividends, splits; "Before 2018" = EOD only. The vendor says to "contact our support team" to confirm coverage for a specific ticker [S10]. Search snippet: "26,000+ US stock tickers (mostly from Jan 2000)" delisted (UNVERIFIED) | "Adjusted Data" is included [S9]. Search snippet: adjusted_close covers splits and dividends, OHLC are raw, and adjusted closes "are recomputed, not stored" (**UNVERIFIED**) | Not point-in-time: adjusted values are recomputed (snippet, UNVERIFIED). Delisted names are found with `delisted=1` on the Exchange Symbol List; renames go through Symbol Change History (US only) [S10] | "Major US Companies: from 1985"; "US Stocks … from earliest available" [S9] | EOD All World **$19.99/mo, $199.00/yr**; All-In-One $99.99/mo, $999.90/yr [S9] | "Personal use"; commercial use needs a separate plan [S9]. Storage and redistribution: nv | 100,000 calls/day, 1,000/min [S9]. Bulk: nv |
| **Alpaca** | **Absent (not documented).** The docs are silent on delisted symbols [S11, S12]. Tier 3 forum reports say inactive symbols return no bars (see Disconfirmation) | `adjustment`: "raw" (default), "split", "dividend", "spin-off", "all" [S12] | Partial: `asof` maps symbols across renames (e.g. FB to META) [S12]. No known-at date for actions is documented | nv (ADR 0003 believes ~2016) | Free: IEX is "the only feed that can be used without a subscription" [S11]. SIP plan price: nv | nv | REST API. Feeds are sip, iex, boats, otc [S12] |

### Evidence rows (template format)

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Norgate delisted names only in Platinum and Diamond | S1 | 1 (vendor pricing page) | "Delisted Securities Back to 1990" / "Back to 1950"; Platinum 12 months = $630, Diamond = $787.50 | n/a | n/a |
| Norgate delisted coverage is complete from late 1992 | S2 | 1 (vendor FAQ) | "essentially complete back to late 1992" | n/a | n/a |
| Sharadar includes delisted names; licence requires deletion | S3 | 1 (authorised reseller's pricing and terms page) | "No survivorship bias: includes active and delisted tickers"; delete "within thirty (30) days of termination" | n/a | n/a |
| CRSP not sold to individuals | S4 | 1 (vendor product page) | "delivered to licensees at academic institutions, government agencies, and investment practitioners" | n/a | n/a |
| Tiingo delisted coverage excludes recycled tickers | S5 | 1 (vendor docs) | "supports delisted data for tickers that have not yet been recycled" | n/a | n/a |
| Tiingo price and licence | S6 | 1 (vendor pricing page) | "$30/month" or "$300/year"; "Internal Use Only" | n/a | n/a |
| Massive price and history depth | S7 | 1 (vendor pricing page) | Advanced "$199/month", "20+ years"; "Non-pros only" | n/a | n/a |
| Massive: no dividend adjustment; adjusted series restated | S8 | 1 (vendor knowledge base) | "Store the unadjusted prices if you are keeping a permanent record" | n/a | n/a |
| EODHD price and plans that include delisted names | S9 | 1 (vendor pricing page) | "$199.00 /year"; "Delisted Data" included | n/a | n/a |
| EODHD pre-2018 delisted names are EOD only | S10 | 1 (vendor docs) | "Before 2018": EOD only | n/a | n/a |
| Alpaca free feed is IEX only | S11 | 1 (vendor docs) | IEX "the only feed that can be used without a subscription" | n/a | n/a |
| Alpaca adjustment options | S12 | 1 (vendor API reference) | "raw" (default), "split", "dividend", "spin-off", "all" | n/a | n/a |

The template's OOS and costs columns do not apply to a vendor survey. Note the tier boundary: the handoff §6.1 classes vendor *marketing* as Tier 3. This brief allows vendor docs, ToS and pricing pages as Tier 1. Survivorship claims are therefore graded **claimed**, never **verified**, because the only source for each is the vendor itself.

### Grade definitions used for delisting coverage
- **Verified:** the vendor documents delisted coverage, **and** an independent Tier 1 source or our own data test confirms it. No vendor reached this grade within budget.
- **Claimed:** the vendor documents or states delisted coverage, with no independent confirmation.
- **Absent:** the vendor does not document delisted coverage, or documents that it lacks it.

## Disconfirmation
- **Searches run** (7 of the 17 searches):
  1. Norgate: "Norgate Data delisted missing data error problem survivorship complaint forum".
  2. Sharadar: "Sharadar SEP data errors bad split adjustment missing delisted tickers closeadj problem".
  3. Tiingo: "Tiingo delisted data missing recycled ticker survivorship bias adjClose wrong".
  4. Massive/Polygon: "Polygon.io delisted tickers missing aggregates wrong split adjustment data quality complaints".
  5. EODHD: "EODHD delisted stocks data quality incorrect adjusted close missing history reddit".
  6. Alpaca: "Alpaca market data delisted symbols not available historical bars survivorship".
  7. Norgate licence: "Norgate Data licence agreement personal use data retained after subscription expires redistribution".

  CRSP was not searched because it is not available to individuals.
- **What was found against.** All of these are Tier 3 flags to verify, not evidence. They come from search-result snippets; none was fetched.
  - **Norgate:** nothing against found. The results were reviews and tutorials, and all were favourable.
  - **Sharadar:** a GitHub issue ([flabber1835/stocker #237](https://github.com/flabber1835/stocker/issues/237)) reports a stale cash-distribution value shared by ACTIONS and the SEP total-return adjustment, "a common-mode vendor error that internal Sharadar corroboration cannot detect". Flag: check dividend adjustments against a second source.
  - **Tiingo:** the vendor's own doc (S5) is the main disconfirmation: recycled tickers lose their delisted history, and permaTicker support is described as future work. A Tier 3 note (via the sp500-data README snippet) describes the general problem of recycled tickers silently resolving to the wrong company.
  - **Massive/Polygon:** GitHub issues [polygon-io/issues #311](https://github.com/polygon-io/issues/issues/311) ("Incorrect stock splits for certain tickers") and [#111](https://github.com/polygon-io/issues/issues/111) ("Multiple stocks missing splits or have bad splits"; snippet cites an erroneous CPRT split on 2017-04-10). A Medium review ([Yolo Trading](https://medium.com/@yolotrading/a-complete-review-of-the-polygon-io-api-everything-you-wanted-to-know-c79e992a74ff)) says Polygon is "not recommended if you need data on delisted tickers", that "delisted_utc" is often missing on inactive tickers, and that SPY dividends from 2020 are missing. A Substack post, "Massive Problems (Part 1)", also exists but was not read. This directly contradicts the vendor's survivorship claim, and the dates of these reports are unknown.
  - **EODHD:** no user reports found. The vendor's own doc limits pre-2018 delisted names to EOD only and asks users to contact support to confirm coverage per ticker (S10).
  - **Alpaca:** forum threads ["Get Historical Data for Inactive Stocks"](https://forum.alpaca.markets/t/get-historical-data-for-inactive-stocks/10097) and ["Delisted tickers"](https://forum.alpaca.markets/t/delisted-tickers/18227) report that symbols from `list_assets(status='inactive')` return no historical bars. A 2023-era forum thread reports the `adjustment` parameter not working. Both are consistent with grading Alpaca **absent**.

## Caveats & gaps
- **No independent verification.** Every "claimed" grade rests on the vendor's word. Only a data test can move a vendor to "verified": pull a sample of known delistings (e.g. from our own EDGAR Form 25 table) and check that each has bars up to its last session.
- **Adjusted prices are not point-in-time at any vendor examined.** Massive and EODHD say explicitly that adjusted series are recomputed as new actions arrive. No vendor documents an announcement or known-at timestamp for corporate actions. Ex-dates are the most any of them gives.
- **Currency conflict for Norgate:** the fetched pricing page reads USD, but a search snippet showed AUD for the same figures. Norgate is an Australian company. At the time of writing, USD 630 vs AUD 630 is roughly a 35% difference.
- **History depth vs. delisted depth differ.** EODHD's delisted names before 2018 have EOD only. Norgate's delisted names are "essentially complete" from late 1992, not 1990. Sharadar starts in 1998.
- **Licence post-cancellation:** Norgate (snippet only) and Sharadar-via-QuantRocket (S3) both require deleting local data when the subscription ends. The other vendors' storage terms were not read.
- Five fetch attempts returned no usable content and are not counted as sources: data.nasdaq.com/databases/SEP (not rendered), sharadar.com (no SEP details), the Sharadar datasheet PDF (unreadable), and two crsp.org URLs (redirected).
- Issue #46's `team:banshee` label was not checked, because this agent had no shell. The brief text came from the orchestrator's message.

## UNVERIFIED items
- Norgate: price currency (USD vs AUD), the post-expiry deletion rule (snippet only), redistribution terms, OS requirements, and point-in-time behaviour of corporate actions.
- Sharadar: price for non-professional users, Nasdaq Data Link direct licence terms, closeadj definition (snippet), the "point-in-time ready" claim (snippet), the ">21,000 tickers" figure (snippet), and API/bulk delivery.
- CRSP: delisted coverage and adjustment method (not evaluated because individual access is unavailable).
- Tiingo: adjustment fields (snippet), storage after cancellation, and bulk download (snippet).
- Massive: the survivorship claim (snippet), corporate actions from 2008 (snippet), which plans include flat files, and licence storage and redistribution terms.
- EODHD: the "26,000+ US delisted tickers" figure (snippet), adjusted_close definition (snippet), storage and redistribution terms, and bulk download.
- Alpaca: history start date, SIP plan price, licence and storage terms, and delisted coverage (docs silent; forum only).

## Follow-up questions (not answered here)
1. Does Norgate's data access (Norgate Data Updater) run on macOS? The owner's machine is macOS. Tier 3 knowledge says the updater is Windows-only (UNVERIFIED). The ADR would need this answered.
2. What is Norgate's billing currency, and what is the exact redistribution clause in its EULA (norgatedata.com/subscribe/eula.php)?
3. What does Sharadar SEP cost a non-professional buying directly on Nasdaq Data Link vs via QuantRocket, and do the direct terms also require deletion on termination?
4. Does Massive's delisted coverage hold up in a sample test, given the Tier 3 reports against it? Which plan includes flat files?
5. A test for each shortlisted vendor: take N Form 25 delistings from the local EDGAR store and check that bars exist up to the final session. This is the only route to a "verified" grade.
6. Can corporate-action known-at timestamps be sourced separately (e.g. 8-K filings), given that no vendor provides them?
7. Do Tiingo, Massive and EODHD let a subscriber keep locally stored data after cancelling?

## Sources
Retrieved 2026-09-25. Tier per the brief's source rules (vendor docs, ToS and pricing pages count as Tier 1).

- **S1** Norgate Data, US Stock Market Packages: https://norgatedata.com/stockmarketpackages.php (Tier 1)
- **S2** Norgate Data, Data Package FAQ: https://norgatedata.com/data-package-faq.php (Tier 1)
- **S3** QuantRocket, Sharadar Data Pricing (authorised reseller; includes licence terms): https://www.quantrocket.com/pricing/data/sharadar/ (Tier 1, reseller)
- **S4** Morningstar Indexes / CRSP, CRSP US Stock Databases: https://indexes.morningstar.com/research-data-products/crsp-us-stock-databases (Tier 1)
- **S5** Tiingo, Symbology documentation: https://www.tiingo.com/documentation/appendix/symbology (Tier 1)
- **S6** Tiingo, Pricing: https://www.tiingo.com/about/pricing (Tier 1)
- **S7** Massive, Pricing: https://massive.com/pricing (Tier 1)
- **S8** Massive, "Is Massive's stock data adjusted for splits or dividends?": https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends (Tier 1)
- **S9** EODHD, Pricing: https://eodhd.com/pricing (Tier 1)
- **S10** EODHD, Delisted Stock Companies Data: https://eodhd.com/financial-apis/delisted-stock-companies-data-2 (Tier 1)
- **S11** Alpaca, Historical Stock Data: https://docs.alpaca.markets/us/docs/historical-stock-data-1 (Tier 1)
- **S12** Alpaca, Historical bars API reference: https://docs.alpaca.markets/us/reference/stockbars (Tier 1)

Search-snippet references (not fetched, flags only, UNVERIFIED): https://norgatedata.com/faq.php, https://data.nasdaq.com/databases/SEP, https://sharadar.com/blog/posts/sharadar-stock-prices-fund-prices-and-adjustments, https://www.tiingo.com/documentation/end-of-day, https://massive.com/stocks, https://eodhd.com/financial-apis/api-for-historical-data-and-volumes, plus the Tier 3 links in Disconfirmation.

**Budget used:** 12 of 12 sources (fetched and cited); 17 of 20 searches; 5 further fetch attempts returned no usable content.

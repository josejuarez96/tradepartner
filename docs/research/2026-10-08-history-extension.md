# Research Report: Extending point-in-time history before mid-2019 (free route vs paid vendors)

**Brief:** #1303  ·  **Date:** 2026-10-08  ·  **Status:** COMPLETE for the question as asked. Several coverage figures can only be settled by a probe, not by reading; they are listed under "UNVERIFIED items".  ·  **Agent/model:** research agent, team histext, claude-opus-5-5

**Budget.** The brief sets no number, so this report set its own cap of 50 web searches and fetches. It used 45: 18 searches and 27 fetches (4 of the fetches returned nothing usable). It also read the repository's earlier reports, ADRs 0003 and 0009, the data-foundation spec, issues #839, #842 and #1301, and two places in the code. All web pages were seen on **2026-10-08**. Vendor prices change, so check them again before any ADR.

**Grading.** Handoff §6.1's verdicts are built for return hypotheses. Their out-of-sample and cost tests do not apply to facts about data sources. So, as in [the EDGAR pitfalls report](2026-10-02-edgar-data-pitfalls.md), each claim here keeps §6.1's source-count rule and drops those two tests:
- **SUPPORTED:** 3 or more independent sources, at least 2 of them Tier 1.
- **MIXED:** the sources conflict.
- **INSUFFICIENT:** fewer than 2 qualifying sources.

Vendor documentation, terms and pricing pages count as Tier 1 for what the vendor *sells and promises*. A vendor's claim about its own coverage stays **claimed** until an independent source or our own data test confirms it ([G8 report](2026-09-25-price-vendors.md), "Grade definitions").

## Answer
**Verdict:** n/a as a single grade, because this is a sourcing brief; each claim is graded in the Evidence table  ·  **Confidence:** high that the free route cannot reach before 2016 and that the pre-2019 listing gap is real; medium on what the free route could recover between 2016 and 2019 (one observed filing per mechanism, coverage not measured); medium on vendor prices and terms (read today); low on any vendor's delisted coverage (vendor's word only)

**Route A (free) can reach back to January 2016 at most, and no further.** Alpaca's free SIP daily bars start on 2016-01-04 and no free source in the earlier reports serves delisted-inclusive prices before that. With a 12-month formation window, that gives a first rebalance around 2017-01-31 instead of today's 2020-08-31: about 84 practice month-ends to 2023-12 instead of 41. Statement fundamentals and shares outstanding are already point-in-time in EDGAR XBRL from 2009 to 2011, so they are not the bottleneck. The bottleneck is **listings**: before the 2019 cover-page rule no filing carries the structured ticker, exchange and class-title triple the security master needs ([#842](https://github.com/josejuarez96/tradepartner/issues/842)). Three free pieces could stand in for it, each observed on one filing only: `dei:TradingSymbol` in pre-2019 XBRL (a ticker, no exchange), Form 8-A12B (a title and an exchange, no ticker), and the presence of SIP bars (exchange-listed trading, but not which exchange). Joining them is new master logic and new inference rules, with risks of the kind #787 already met.

**Route B (paid) is the only way to get about 25 years.** Of the vendors an individual can buy, **Sharadar** is the only one found that sells in one package, from 1998, prices including delisted names, corporate actions with listing and delisting dates, and fundamentals with an **as-reported** dimension "time-indexed to the date the form 10 regulatory filing was submitted to the SEC". The full-history bundle is **USD 499/yr**. Its licence requires deleting the data within 30 days of cancellation, though it lets you keep backtest results. Norgate (USD 630/yr) has delisted prices to 1990 but only current fundamentals according to third-party docs, and a Windows-only updater. EODHD's delisted names before 2018 have prices only and no fundamentals. QuantConnect's data is free only inside its cloud; local daily prices cost USD 2,136/yr plus USD 600/yr for the security master.

## Evidence

| # | Claim | Source | Tier | Key figure (quoted) | Grade |
|---|---|---|---|---|---|
| E1 | Free daily bars start 2016-01-04, nothing earlier | S1 (#104 probe); S2 G8 S30 (Alpaca pricing); S14 (Alpaca forum) | 1 / 1 / 3 | S1: SPY and KO "each return 19 bars, the first on 2016-01-04. Nothing is returned for December 2015"; S2: "7+ years"; S14 (search summary): "SIP data is available from 2016" | SUPPORTED |
| E2 | Free bars include some delisted names, verified only on 8 names delisted 2019 to 2023 | S1; S4 ADR 0009 Decision 2 | 1 (own probe) | S1: "All 8 delisted names probed … return raw SIP daily bars that end on their last regular session"; ADR 0009: "not measured for 2016 to 2018 delistings, small caps or reused tickers" | INSUFFICIENT for 2016 to 2018 delistings (not probed) |
| E3 | Structured ticker, exchange and title on filings start only in mid-2019 to 2021 | S8 (SEC FAST Act guide); S3 F14; S5 (#842) | 1 / 1 / 1 (own store) | S8: cover pages must disclose "the national exchange or principal U.S. market for their securities, the trading symbol, and title of each class", tagged in Inline XBRL; phase-in "Large accelerated filers … June 15, 2019; Accelerated filers … June 15, 2020; All other filers: June 15, 2021"; #842: universe 0 at 2017-01-31, 2018-06-29 and 2019-06-28; 1,000 at 2019-12-31 | SUPPORTED |
| E4 | Before 2019, an issuer's XBRL can still carry its ticker (`dei:TradingSymbol`), with no exchange | S9 (Apple FY2016 10-K, R1 cover report) | 1 (primary filing) | "Trading Symbol: AAPL"; "Entity Common Stock, Shares Outstanding: 5,332,313 (in thousands)"; the table has no exchange row | INSUFFICIENT (one filing; how many filers tagged it is not measured) |
| E5 | Our parsers drop a ticker that has no title or exchange, and count it | S34 (`adapters/edgar.py`, `parse_cover_page` docstring and the FSN path) | 1 (code) | "a symbol with no title or no exchange is skipped and counted in `incomplete_listings` (owner decision #224, as the FSN path)" | n/a (fact about our code) |
| E6 | Form 8-A12B states the class title and the exchange, with no ticker | S10 (G1 Therapeutics 8-A12B, 2017-05-15); search summary of SEC Form 8-A (S11) | 1 / 1 (snippet) | "Title of each class to be so registered: Common Stock, par value $0.0001 per share \| Name of each exchange on which each class is to be registered: The NASDAQ Stock Market LLC"; no ticker anywhere in the form | INSUFFICIENT (one filing read; Form 8-A text from a snippet) |
| E7 | XBRL shares and statement data cover every filer from fiscal periods ending after 2011-06-15 | S12 (law-firm and trade-press summaries of SEC Release 33-9002); S3 F2, F17 | 2 / 1 | phase 1 "June 15, 2009" (float above $5 billion), phase 2 "June 15, 2010", phase 3 "June 15, 2011" for "all other filers including smaller reporting companies"; FSN "Submitted from 4/15/2009", "All data values are 'as filed.'"; Apple's 2009 10-K cover shares fact in companyfacts | SUPPORTED |
| E8 | The store already reads FSN from 2015 | S34 (`config.py:271`) | 1 (code) | `fsn_first_year: int = Field(default=2015, ge=2009)` | n/a |
| E9 | Alpaca's free corporate actions include symbol changes with CUSIPs | S13 (Alpaca API reference) | 1 | types include "name_change, worthless_removal" and "cash_merger"; a name_change carries "old_symbol, old_cusip, new_symbol, new_cusip, process_date"; history depth is not stated | INSUFFICIENT for depth (one source, silent) |
| E10 | A symbol-keyed free source can mix two companies under one reused ticker | S15 (Alpaca forum, 2025-11-25, staff reply 2025-12-01); S6 (#787 amendment in the data-foundation spec) | 3 / 1 (own store) | S15: "Q" held Quintiles IMS 2016 to 2017 and Qnity 2025; staff: "we were missing the new 'Q' … The issue now has been fixed", and `asof < 2017-11-15` returns the old company; spec #787: "+1,216% (BTU) or +12,900% (SD)" one-day moves | INSUFFICIENT by the count rule (two sources), though both are observed cases, one in our own store |
| E11 | Sharadar sells prices and fundamentals from 1998, active and delisted, with an as-reported dimension stamped at the SEC filing date | S16 (docs/fundamentals); S17 (docs/actions); S18 (homepage) | 1 (vendor docs) / 3 (homepage) | AR dimensions "excludes restatements" and give a "point-in-time view with data time-indexed to the date the form 10 regulatory filing was submitted to the SEC"; MR "includes restatements"; "nearly 18,000 active and delisted US public companies", history to "January 1998", "Common stock securities (primary class)"; actions: "Deep history to 1998", including "ticker changes, … listing dates, delisting dates, delist reasons, acquisition counterparties"; homepage: "nearly completely free of survivorship bias" | **Claimed** (vendor only) |
| E12 | Sharadar's coverage start for fundamentals is stated differently by the reseller | S19 (QuantRocket Sharadar page) against S16 | 1 (reseller) / 1 | QuantRocket: "corporate fundamentals for US stocks with history since 1990"; Sharadar: "January 1998" | MIXED |
| E13 | Sharadar's tickers table is a current snapshot, not a dated listing history | S20 (docs/tickers) | 1 | fields include `permaticker` ("a unique and unchanging identifier"), `exchange`, `isdelisted`, `firstpricedate`, `lastpricedate`, `cusips`, `relatedtickers`; no dated exchange or ticker history is documented | INSUFFICIENT (one source; read as absence) |
| E14 | Sharadar price, Oct 2026 | S21 (sharadar.com/subscribe) | 1 | Bundle "$29/$49/$69" monthly, "$299/$399/$499" annual for 5 years, 10 years, full history; "Full History Bundle is $69/month ($499/year)"; Prices full history "$39/$299"; Fundamentals full history "$39/month, $399/year"; "Personal Use License" | n/a (price fact) |
| E15 | Sharadar licence: personal use, delete within 30 days of termination, derived works may be kept | S22 (sharadar.com/terms); S2 S25 (same clause on 2026-09-25) | 1 / 1 | "natural persons acting in an individual capacity only"; "Within thirty (30) days of termination, delete from all computer systems you own or operate all copies of the Services Data"; keep "research outputs, backtest results, models, summary statistics, trade logs, and similar derived works that do not contain and cannot reproduce the Services Data" | n/a (licence fact; stable across two reads) |
| E16 | Norgate: delisted prices to 1990; fundamentals are current values only | S23 (Norgate packages); S24 (RealTest docs) | 1 / 3 | Platinum "Back to 1990", "USD 630" per 12 months, with delisted securities and "historical index constituents"; Diamond "Back to 1950", "USD 787.50"; RealTest: "Norgate provides more than 150 current fundamental data items for each stock" | Prices: **claimed**. Fundamentals current-only: INSUFFICIENT (Tier 3 only; Norgate's own page does not say) |
| E17 | EODHD: delisted names before 2018 have prices only; fundamentals from 1985 for major US names; basis not stated | S25 (delisted page); S26 (fundamentals page); S27 (pricing) | 1 | "Before 2018: EOD only"; "After 2018: EOD, Fundamentals, Dividends and Splits"; "Major US companies are covered from 1985"; as-reported or restated is not stated; each report has a `filing_date`; Fundamentals Data Feed "$59.99" monthly, "$599.90" annual; All-In-One "$999.90" annual | **Claimed** |
| E18 | QuantConnect: fundamentals "As Original Reported" from 1998, with filing dates approximated for older names; local data is priced separately | S28 (QC Morningstar dataset); S29 (QC forum, staff); S30 (QC LEAN CLI docs) | 1 / 3 / 1 | "8,000 US Equities, starts in January 1998"; "all the data is loaded using 'As Original Reported' figures"; "For older symbols, the file date is approximated 45 days after the as of date"; staff: "Yes; ish. It is intended to be and was at one point, but they changed it without notifying anyone and now have issues" (undated); local daily prices "$2,136" per year, updates "$600/year", security master "$600" | MIXED on point-in-time quality |
| E19 | Tiingo: fundamentals 20+ years, 5,500+ equities, as-reported toggle; price not published | S31 (Tiingo fundamentals docs) | 1 | "Data goes back over 20 years"; "5,500+ Equities Covered"; `asReported=true`; "an add-on subscription", price not on the page | **Claimed**; price UNVERIFIED |
| E20 | Standardised vendor fundamentals differ from as-filed data enough to change research results | S32 (Du, Huddart and Jiang 2023, JAE 76(1)); S33 (Lyle, Siano and Yohn 2024, working paper); E18's staff reply | 1 / 1 / 3 | S32: discrepancies between as-filed and Compustat data affect "the existence and magnitude of six of 21 accounting-based anomalies examined"; "FactSet data also exhibit significant and often larger discrepancies from as-filed data"; S33: re-standardisation "introduces a form of look-ahead bias" and "precise replication … is nearly impossible" | SUPPORTED |

## Route A: the free route, in detail

**What it can reach.** Prices bind it to 2016-01-04 (E1). Fundamentals and shares do not: every filer has XBRL statements and cover shares from fiscal periods ending after 2011-06-15 (E7), FSN data are "as filed" (E7), and the store already reads FSN from 2015 (E8). So "statement fundamentals before mid-2019" is mostly a matter of what is already loaded, not of new sources. That the store's `statement_facts` already hold 2016 to 2019 was not checked here (UNVERIFIED).

**What is missing: listings from 2016 to mid-2019.** The master takes ticker, exchange and title only from cover-page triples, which exist from fiscal periods ending after 2019-06-15 for large accelerated filers and after 2021-06-15 for the smallest (E3). Before that, the only listings are `snapshot_static` rows stamped at fetch time, which are invisible at earlier T (data-foundation spec, master column table; #842). Three free pieces could rebuild a point-in-time listing before 2019. Each is stamped at the acceptance or session close, so none needs look-ahead, but each covers only part of the triple:

| Piece | Gives | Known at | Missing | Observed |
|---|---|---|---|---|
| `dei:TradingSymbol` in 10-K and 10-Q XBRL (FSN `txt`, 2009 on) | ticker, usually one per filer, no class | filing acceptance | exchange; class title; a second class's ticker | One filing (E4). Our parsers already see it in FSN periods from 2015 and drop it as `incomplete_listings` (E5), so the backfill logs may already hold the count |
| Form 8-A12B | class title and exchange | acceptance | ticker; issuers registered before EDGAR (before about 1996) never file one in the window | One filing (E6) |
| Pre-2019 10-K cover page (HTML text) | "Title of each class" and "Name of each exchange" for 12(b) classes | acceptance | ticker (added by the 2019 rule, E3); text parsing, not tags | Not read here (UNVERIFIED) |
| Form 25 / 25-NSE (already ingested) | the exchange a class leaves | acceptance | only at the end of a listing, so it cannot date the start | In use |
| SIP bars from Alpaca | the symbol traded on a national exchange that session (SIP carries exchange-listed names; OTC is a separate feed) | session close | which exchange; which company, when a ticker is reused (E10) | In use |
| Alpaca `name_change` actions | old and new symbol and CUSIP | process date | depth before 2019 unknown; no announcement time | Not probed (E9) |

**What the join would take (estimates, not measured).** New master rules to combine a filer's XBRL ticker with an exchange from an 8-A12B, a pre-2019 10-K cover or, failing both, "traded on SIP"; a parser for 8-A12B and pre-2019 10-K cover text; a rule for dual-class issuers, where `dei:TradingSymbol` names one class only; and re-running the resolver and the #787 price-quality gate over 2016 to 2019, the period where ticker reuse caused the BTU and SD jumps. That is a spec amendment (the master column table) and probably two to four plan tasks, each S or M (a judgement, not a measurement). The gap metric's `stale_listings` and `no_bar_at_t` counts would be the check that it worked.

**Risks.**
1. **Coverage is unknown.** One filing per mechanism was read (E4, E6). If many small filers did not tag `dei:TradingSymbol` before 2019, the universe in 2017 to 2018 would lean towards large, well-documented names, which is a selection bias of its own.
2. **Delisted coverage for 2016 to 2018 is unprobed** (E2). Momentum's survivorship bias sits in those names (ADR 0003, Context).
3. **Ticker reuse.** A symbol-keyed source served two companies under "Q" until a user reported it (E10). The 2016 to 2019 stretch is where our store already found such cases.
4. **Exchange inference.** "Traded on SIP" cannot tell NYSE American from NYSE Arca or Cboe, and the universe is NYSE, Nasdaq and NYSE American (charter). A wrong exchange admits or drops a name.
5. **The result is still short.** About 84 practice month-ends (2017-01 to 2023-12) and the same 33-month exam. Splitting that into more than one exam slice leaves each slice shorter.
6. **Alpaca can change the free plan** (ADR 0009, triggers).

## Route B: paid vendors, compared

Prices are as read on 2026-10-08 unless marked. "Claimed" means only the vendor says so.

| Vendor | Coverage start | Delisted coverage | Point-in-time quality | Listing history | Licence for personal use | Cost per year | Format / API | Mac fit |
|---|---|---|---|---|---|---|---|---|
| **Sharadar** (direct) | Prices and fundamentals Jan 1998 (E11); reseller says fundamentals 1990 (E12) | Claimed, "nearly 18,000 active and delisted" companies, "nearly completely free of survivorship bias" (E11) | Fundamentals: as-reported dimension, excludes restatements, indexed to the filing **date** (a day, not a time) (E11). Adjusted prices restated backwards; raw `closeunadj` kept (G8 S16). Corporate actions have one `date`, no announcement date (E11, S17) | Tickers table is a snapshot with `permaticker`; dated history only through actions (ticker changes, listing and delisting dates) (E11, E13) | Natural persons only; delete within 30 days of termination; derived results may be kept; no redistribution (E15) | **Bundle full history 499** (prices + fundamentals + tickers + actions); Prices alone 299; Fundamentals alone 399 (E14) | API and bulk download (G8 S22) | Yes (files and API) |
| **Norgate** Platinum / Diamond | 1990 / 1950 (E16) | Claimed; "essentially complete back to late 1992" (G8 S2) | Prices: four adjustment modes incl. raw (G8 S2). Fundamentals: current values only (Tier 3, E16) | Historical index constituents (E16); dated listing history not documented | Personal use only; delete on expiry; two machines (G8 S14, S19) | **630 / 787.50** (E16) | Windows-only updater; Python package (G8 S21) | VM needed |
| **EODHD** | Prices: US common stocks from 1962; fundamentals "from 1985" for major US names (E17, G8 S15) | Before 2018: prices only; after 2018: prices, fundamentals, splits, dividends (E17) | Basis (as-reported or restated) not stated; `filing_date` per report (E17). Adjusted closes "recomputed, not stored" (G8 S15) | Not researched | Personal, non-commercial; deletion clause unresolved (G8) | Prices **199**; Fundamentals feed **599.90**; All-In-One **999.90** (E17) | API, bulk by exchange-day (G8 S29) | Yes |
| **QuantConnect** (AlgoSeek prices, Morningstar fundamentals) | 1998 (E18; prices from search summary) | Prices "survivorship bias-free" (search summary, not fetched) | "As Original Reported", but older filing dates "approximated 45 days after the as of date"; staff say point-in-time broke at some point (E18) | Security master with "splits, dividends, and symbol changes" (E18) | Not read | Cloud: free. Local daily prices **2,136** + updates 600 + security master **600**; local fundamentals price not found (E18) | LEAN files; cloud-first | Yes (CLI) |
| **Tiingo** | Fundamentals "over 20 years"; prices "30+ Years" (E19, G8 S6) | Claimed for tickers "that have not yet been recycled" (G8 S5) | As-reported toggle (E19); filing date not documented | `permaTicker` (E19) | Delete on cancellation (G8 S24) | Prices 300 (G8 S6); fundamentals add-on **not published** | REST API | Yes |
| **Massive** (Polygon) | Prices "20+ years" on the Advanced plan (G8 S7) | Claimed; disputed by Tier 3 reports (G8) | Adjusted series restated; corporate actions from 2008 (G8 S8, S13); fundamentals not researched here | Not researched | Delete at termination (G8 S26) | About 1,910 to 2,388 (G8, read 2026-09-25, not re-read) | API, flat files | Yes |
| **CRSP / Compustat** | 1925 / 1950s | The academic standard | Compustat restates (E20); Snapshot is point-in-time | CRSP dated names and exchanges | Institutions only (G8 S4) | Not sold to individuals | n/a | n/a |

What a paid vendor adds over Route A, and what it does not:
- **Adds:** history from 1998 (about 25 practice years before the 2024 exam, room for several exam slices), delisted names claimed from 1998, and a dated listing history from corporate actions.
- **Does not add:** acceptance timestamps. Sharadar stamps as-reported data by filing *date* and actions by one date; no vendor documents when a corporate action became known (G8). The store would still need a stamping rule (ADR 0003 rule 2 style) for vendor rows.
- **For 2009 onwards, EDGAR stays the better fundamentals source:** as-filed, stamped to the second, and free (E7, E20). A vendor's as-reported fundamentals matter mainly for 1998 to 2010, before full XBRL coverage.

## Disconfirmation
- **Searches run** (the disconfirmation subset of the 18):
  1. Point-in-time versus restated fundamentals and look-ahead bias (Compustat snapshot, data revisions).
  2. Sharadar data-quality problems with delisted tickers.
  3. Sharadar ARQ `datekey` problems or wrong filing dates (QuantRocket forum).
  4. Alpaca historical bars for delisted stocks in 2017 and 2018 returning no data.
  5. Norgate fundamentals as current values only, not history (two searches).
  6. QuantConnect Morningstar point-in-time quality.
  7. A free historical ticker-change list (Nasdaq Trader).
- **What was found against:**
  1. **Vendor point-in-time claims can break silently.** QuantConnect's staff say Morningstar's point-in-time data "was at one point" correct, then changed "without notifying anyone" (E18). For older names, QuantConnect's filing date is an approximation (45 days), not a record. A vendor's "as reported" label is therefore not, on its own, evidence of correct `known_at`.
  2. **Standardised fundamentals change results** (E20). Two Tier 1 papers show vendor standardisation moves anomaly results. That argues for EDGAR as-filed data wherever it exists (2009 on) and for the as-reported dimension of any vendor before that.
  3. **Sharadar's own numbers disagree with its reseller's** (E12: 1990 against 1998), and its homepage hedges ("nearly completely free of survivorship bias"). Its delisted coverage stays **claimed**. The G8 report found one Tier 3 issue on a stale Sharadar cash distribution. No Sharadar `datekey` complaint was found (search 3 returned nothing relevant).
  4. **Free symbol-keyed data mixes companies** (E10). The "Q" case was 2016 to 2017 data, in the period Route A would add.
  5. **Norgate fundamentals:** only Tier 3 tooling documents say they are current values. One forum thread with that title returned HTTP 403. Not confirmed on norgatedata.com.
  6. **Free ticker history:** a Tier 3 forum says Nasdaq no longer publishes its symbol-change history. No free dated ticker list was found.
  7. **Alpaca and delisted names, 2016 to 2018:** nothing found either way. The forum results concern the IEX feed ("IEX bar data is only available from 2020") or names gone to OTC.
- **Nothing found against** E3 (the 2019 cover-page rule) or E7 (the XBRL phase-in).

## Recommendation (evidence-graded options for the owner; not a decision)

**In plain words.** The free route can add about three and a half years of practice data (back to early 2017), costs no money, and needs a few careful build tasks (an estimate, not a sized plan) that teach the system which ticker belonged to which company before 2019. It can never go back further than 2016, because no free source has prices for companies that have since disappeared before that year. The only way to get about 25 years, enough to hold out more than one exam period, is to pay a vendor. The cheapest credible one found is Sharadar at USD 499 a year for prices and fundamentals from 1998. That needs you to change today's USD 0 data budget (ADR 0009), and to accept that if you stop paying you must delete its data within 30 days. You keep your backtest results, but can no longer rerun them. Neither route has been tested on our own data yet, so the evidence favours one cheap measurement of each before choosing.

What the evidence supports, in order:
1. **Measure Route A before building it (read-only, no cost).** Count, for 2016 to 2019, how many issuers' FSN filings carry a `dei:TradingSymbol` without an exchange (our parsers already count these as `incomplete_listings`, E5), and probe Alpaca SIP bars for a sample of Form 25 names delisted in 2016 to 2018 (the #104 method). If ticker coverage of the universe-sized names is low, or delisted bars are missing, Route A buys little for its build cost.
2. **If several exam slices are the goal, only Route B delivers them.** Route A's 2017 to 2023 window is too short to split into practice plus a second exam of useful length.
3. **If Route B is chosen, start with one month** of the Sharadar bundle (USD 69, E14), as ADR 0009 option 7 already plans: reconcile its delisted names and 2016 to 2023 closes against Alpaca and EDGAR Form 25, and its as-reported `datekey` against our EDGAR acceptance times for 2011 onward. Only that test can move its coverage from "claimed" to "verified".
4. **Keep EDGAR as the fundamentals source from 2009 on** under either route (E7, E20); take vendor fundamentals only for the years before XBRL.

**Owner decisions this needs:**
1. **Budget:** keep USD 0 (Route A only, history from 2016) or amend ADR 0009 to a data budget of about USD 500/yr (Route B). The amendment is a class B ADR.
2. **Deletion clause:** accept that vendor rows in the store are deleted within 30 days of cancellation, and decide what the Parquet export and tagged artefacts may contain (ADR 0009 Decision 5 already names this). The repository is public, so vendor data never enters it under any route.
3. **Stamping rule for vendor rows:** what `known_at` a filing-date-only fundamental and a one-date corporate action get (for example, the close of the session after the date), since no vendor gives a time (G8; E11).
4. **Route A inference rules:** whether a listing may be built from a ticker in one filing and an exchange from another filing or from SIP trading. This amends the data-foundation spec's master column table.
5. **Practice and exam windows:** how the longer history would be split, and how already-registered hypotheses (H1's frozen keys, #1301) are treated. Every re-split is a new registration.
6. **Authorising the probes:** the read-only Route A count and the 2016 to 2018 delisting probe (no cost), and, if Route B is chosen, the one-month Sharadar cross-check (USD 69).

## Caveats & gaps
- **The free route's coverage is not measured.** Each mechanism rests on one filing (E4, E6). That is enough to show a mechanism exists, not how much of the universe it covers.
- **Effort figures are judgements.** The plan-task count for Route A is an estimate from the spec, not a sized plan.
- **Vendor prices change.** All were read on 2026-10-08 except Massive (2026-09-25, G8). Sharadar's annual Prices price (USD 299), unresolved in G8, is now on its page (E14).
- **No vendor's delisted coverage is verified.** Everything in the Route B table is the vendor's word plus third-party tooling notes.
- **Not researched:** Financial Modeling Prep, Intrinio, Zacks via Nasdaq Data Link, Massive's fundamentals, and any free pre-2016 price source with delisted names (Stooq, Yahoo). Earlier reports did not find a free one.
- **Tier note.** E12's 1990 figure is from a reseller's marketing page; the vendor's own docs (1998) are the better source.
- The Lyle, Siano and Yohn paper (S33) was read from its abstract only. The PDF did not parse here.

## UNVERIFIED items
- Share of 2016 to 2019 filers whose XBRL carries `dei:TradingSymbol`, and whether FSN `txt` for those periods holds it (E4; one filing, read through the R1 cover report, not FSN).
- Whether the store's backfill logs already record the pre-2019 `incomplete_listings` count (E5).
- Alpaca SIP bars for names delisted 2016 to 2018; depth of Alpaca `name_change` actions before 2019 (E2, E9).
- That the store's `statement_facts` already cover 2016 to 2019 (E8 shows only the FSN start year in config).
- Pre-2019 10-K cover pages listing "Name of each exchange on which registered" (form text not read here).
- Sharadar's fundamentals start (1990 or 1998, E12); its delisted completeness; how its `datekey` compares with EDGAR acceptance times.
- Norgate fundamentals being current-only (Tier 3 only, E16).
- QuantConnect local fundamentals price and licence terms; AlgoSeek's "survivorship bias-free" and 1998 start (search summary only).
- Tiingo fundamentals add-on price.
- XBRL phase-in dates are from law-firm and trade-press summaries (search summary), not the SEC release itself.

## Follow-up questions (not answered here)
- How long a practice window, and how many exam slices, does a given number of strategy comparisons need (minimum backtest length, deflated Sharpe)? This decides whether Route A's extra years are enough. It is a separate research brief.
- Is there any free source of delisted-inclusive US daily prices before 2016 that passes a #104-style probe?
- Could 8-K press-release text ("(NYSE: XYZ)") date ticker and exchange changes before 2019 better than 10-K covers?
- Does Sharadar's `permaticker` and actions history map cleanly onto our CIK-keyed master, or does each vendor need a second identity crosswalk?

## Sources
Seen on 2026-10-08 unless noted. "Local" means this repository.
- **S1** Local: [2026-09-25-alpaca-delisted-bars.md](2026-09-25-alpaca-delisted-bars.md) (#104 probe) (Tier 1, own observation)
- **S2** Local: [2026-09-25-price-vendors.md](2026-09-25-price-vendors.md) (G8; its source numbers cited as "G8 Sn"; read 2026-09-25) (Tier 1 via its sources)
- **S3** Local: [2026-09-25-cover-page-facts-at-scale.md](2026-09-25-cover-page-facts-at-scale.md) (F-numbers) (Tier 1 via its sources)
- **S4** Local: [ADR 0009](../decisions/0009-price-vendor.md); [ADR 0003](../decisions/0003-data-adapters-local-first.md) (Tier 1, own decisions)
- **S5** Issues [#842](https://github.com/josejuarez96/tradepartner/issues/842), [#839](https://github.com/josejuarez96/tradepartner/issues/839), [#1301](https://github.com/josejuarez96/tradepartner/issues/1301) (Tier 1, own observations and owner decisions)
- **S6** Local: [data-foundation spec](../specs/data-foundation.md), master column table and the #787 amendment (Tier 1)
- **S8** SEC, FAST Act Modernization and Simplification of Regulation S-K, small business compliance guide: https://www.sec.gov/info/smallbus/secg/fast-act-modernization-and-simplification-of-regulation-s-k (Tier 1)
- **S9** SEC EDGAR, Apple Inc. 10-K for fiscal 2016, cover report R1: https://www.sec.gov/Archives/edgar/data/320193/000162828016020309/R1.htm (Tier 1, primary filing)
- **S10** SEC EDGAR, G1 Therapeutics Form 8-A12B, 2017-05-15: https://www.sec.gov/Archives/edgar/data/1560241/000119312517170896/d193025d8a12b.htm (Tier 1, primary filing)
- **S11** SEC Form 8-A: https://www.sec.gov/file/form8a (Tier 1; search summary only, not fetched)
- **S12** XBRL phase-in under SEC Release 33-9002, via search summaries of Journal of Accountancy (March 2009) https://www.journalofaccountancy.com/issues/2009/mar/financialreporting-mar-2009/ and Cahill Gordon https://www.cahill.com/publications/client-alerts/000136 (Tier 2; not fetched)
- **S13** Alpaca, Corporate actions API reference: https://docs.alpaca.markets/reference/corporateactions-1 (Tier 1)
- **S14** Alpaca forum, "Cannot get Historical Bar data before 2020": https://forum.alpaca.markets/t/cannot-get-historical-bar-data-before-2020/12415 (Tier 3; search summary only)
- **S15** Alpaca forum, "Historical bars of symbol 'Q' are comprised of two different stocks" (2025-11-25, staff reply 2025-12-01): https://forum.alpaca.markets/t/fixing-data-historical-bars-of-symbol-q-are-comprised-of-two-different-stocks/18184 (Tier 3)
- **S16** Sharadar, Fundamentals documentation: https://sharadar.com/docs/fundamentals (Tier 1, vendor doc)
- **S17** Sharadar, Corporate actions documentation: https://sharadar.com/docs/actions (Tier 1, vendor doc)
- **S18** Sharadar, homepage: https://sharadar.com/ (Tier 3, marketing)
- **S19** QuantRocket, Sharadar page: https://quantrocket.com/sharadar (Tier 1 reseller; marketing figures)
- **S20** Sharadar, Tickers documentation: https://sharadar.com/docs/tickers (Tier 1, vendor doc)
- **S21** Sharadar, Subscribe (pricing): https://sharadar.com/subscribe (Tier 1)
- **S22** Sharadar, Terms of Use: https://sharadar.com/terms (Tier 1)
- **S23** Norgate, US Stock Market Packages: https://norgatedata.com/stockmarketpackages.php (Tier 1)
- **S24** RealTest documentation, Norgate fundamentals: https://mhptrading.com/docs/topics/idh-topic10755.htm (Tier 3, tooling)
- **S25** EODHD, Delisted Stock Companies Data: https://eodhd.com/financial-apis/delisted-stock-companies-data-2 (Tier 1)
- **S26** EODHD, Fundamental Data API: https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds (Tier 1)
- **S27** EODHD, Pricing: https://eodhd.com/pricing (Tier 1)
- **S28** QuantConnect, US Fundamental Data (Morningstar): https://www.quantconnect.com/data/morning-star-us-fundamentals (Tier 1, vendor doc)
- **S29** QuantConnect forum, "Is 'US Fundamental Data - Morningstar' point-in-time data?": https://www.quantconnect.com/forum/discussion/14856/is-quot-us-fundamental-data-morningstar-quot-point-in-time-data/ (Tier 3; staff reply, undated)
- **S30** QuantConnect, LEAN CLI US Equity dataset: https://www.quantconnect.com/docs/v2/lean-cli/datasets/quantconnect/us-equity (Tier 1, vendor doc)
- **S31** Tiingo, Fundamentals documentation: https://www.tiingo.com/documentation/fundamentals (Tier 1)
- **S32** Du, K., Huddart, S. and Jiang, X. D. (2023), "Lost in standardization: Effects of financial statement database discrepancies on inference", *Journal of Accounting and Economics* 76(1): https://pure.psu.edu/en/publications/lost-in-standardization-effects-of-financial-statement-database-d/ (Tier 1; abstract)
- **S33** Lyle, M., Siano, F. and Yohn, T. L. (July 2024), "Re-Standardized Financial Statement Data", working paper: https://som.yale.edu/sites/default/files/2024-07/Re-Standardized%20Financial%20Statement%20Data.pdf (Tier 1; abstract via search summary, PDF not parsed)
- **S34** Local code: `src/tradepartner/adapters/edgar.py` (`parse_cover_page` docstring, lines about 753 to 757; FSN listing path, about 1058 to 1063); `src/tradepartner/config.py:271` (Tier 1)
- Fetch attempts that returned nothing usable (not counted as sources): QuantRocket Sharadar docs (no detail), the EDGAR Filer Manual vol. 2 v13 PDF (too large), the QuantConnect bulk-download page (404), the AmiBroker forum thread on Norgate fundamentals (HTTP 403).

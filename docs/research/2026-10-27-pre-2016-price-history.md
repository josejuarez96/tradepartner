# Research Report: What it would take to get US equity daily prices from before 2016

**Brief:** orchestrator brief of 2026-10-27 (owner question of the same day; no issue number)  ·  **Date:** 2026-10-27  ·  **Status:** COMPLETE for the four questions, at the depth noted. Several coverage and event-count figures can be settled only by a probe or a read-only store count, which this run could not make (no shell). They are listed under "UNVERIFIED items".  ·  **Agent/model:** research subagent, claude-opus-5-5

**Budget.** The brief set no number, so this report set its own cap of 20 web calls. It used 18: 8 searches and 10 fetches (2 fetches returned nothing usable: Sharadar's `docs/prices` page returned 404 and Stooq's bulk page returned empty). It read the repository's earlier reports first and does not redo them: [G8 price vendors](2026-09-25-price-vendors.md) (2026-09-25), [history extension and the Route A measurement](2026-10-08-history-extension.md) (#1303, #1311, 2026-10-08), ADRs [0005](../decisions/0005-objective-benchmark-stop-criteria.md), [0009](../decisions/0009-price-vendor.md), [0016](../decisions/0016-development-boundary-and-forward-exams.md), [0022](../decisions/0022-no-vendor-security-master.md) and [0023](../decisions/0023-break-first-fast-books-paper-exam.md), the charter's constraints, the data-foundation plan's unbuilt vendor tasks (T144 to T150), the [data-releases](../runbooks/data-releases.md) and [benchmark-backfill](../runbooks/benchmark-backfill.md) runbooks, the research-registry spec (req 4), the [sharper-candidates report](2026-10-27-sharper-candidates.md) and the [C1](../experiments/c1-filing-delay.md) and [C1b](../experiments/c1b-filing-delay-matched.md) experiment files. Web pages were seen on **2026-10-27**. No database was opened and `.env` was not read.

**Grading.** As in the history-extension report, this is a sourcing brief, so handoff §6.1's out-of-sample and cost tests do not apply to facts about data sources. Each claim keeps §6.1's source-count rule: **SUPPORTED** needs 3 or more independent sources, at least 2 of them Tier 1. **MIXED** means the sources conflict. **INSUFFICIENT** means fewer than 2 qualifying sources. A vendor's own docs, terms and pricing are Tier 1 for what it *sells and promises*. Its coverage claim stays **claimed** until an independent source or our own probe confirms it.

## Answer
**Verdict:** n/a as one grade (a sourcing brief; each claim is graded in the Evidence table)  ·  **Confidence:** high that no free source meets the bar and that Sharadar is still the cheapest credible vendor; medium on prices and terms (read today); low on any vendor's delisted coverage (the vendor's word only); low on the event-count estimates (arithmetic from repo counts, not a store read)

1. **No free source qualifies.** Alpaca's history starts on 2016-01-04 on every plan. Stooq could not be verified, and the only Tier 3 description treats it as survivor-only. Nothing free carries delisted names with corporate actions before 2016. The earlier reports reached the same conclusion.
2. **One paid source fits best: Sharadar direct, Prices plan, Full History, USD 39 a month or USD 299 a year** (re-read today). It covers stocks and funds from 1998, active and delisted (claimed), and includes the `tickers` and `actions` tables. It comes as a bulk file or an API, and the licence lets backtest results survive cancellation. The full bundle (adds fundamentals) is USD 69 a month or USD 499 a year. EDGAR already covers fundamentals from 2009, so the bundle adds little for these candidates. Norgate (USD 630/yr, Windows only), EODHD (USD 199/yr; delisted names before 2018 are prices only, with no splits or dividends) and Massive (USD 199/mo) are worse fits for the reasons in the table. Tiingo by its own account drops recycled tickers.
3. **The binding constraint is not the vendor. It is our own rules and identity.** The research-registry spec (req 4) lets a return run read prices only from the store's as-of API, "never through an adapter". So vendor bars have to enter the store. ADR 0022 says Sharadar "never becomes a store source", and ADR 0023 D7 re-opens the vendor question only for "a rung-4 survivor that needs pre-2016 history". C1 and C2 are at rungs 1 and 2, so neither has triggered it. Getting the data in means a new ADR, then the shelved T146 crosswalk (S), the T147 fill-only adapter (M) and the T149 health counts (S), then an owner backfill under a named release (T149b). That is about 3 build tasks, 2 owner sittings and the ADR. A full universe before 2019 (T148, listing history) is extra and not needed for event studies.
4. **What it unlocks, roughly.** For **C1** the store already holds filings from 2009, so vendor bars would add about 5.5 years (2010-07 to 2016-06) to C1b's 7.25. At today's 50 to 60% bar coverage, that is about 70k to 100k more events before filters, against C1b's 100,895 (estimate). The bigger change is survivorship. Today 4 of 1,301 issuers that stopped filing in 2017 to 2019 have any bars, so every C1 result is "killed on survivor data". Delisted bars would remove that caveat, inside 2016 to 2023 as well. For **C2**, pre-2016 spin-offs are no denser than recent ones. A proportional scale-up from the brief's 8 to 12 events gives roughly 25 to 50 in total back to 2000. That is still underpowered for a 12-month hold (at about 45% dispersion per event, 40 events give a standard error of about 7 pp). C2's CSD rung-2 read for 2007 to 2015 does need pre-2016 ETF bars, which the vendor's fund table supplies. **C3** gains a 2001 to 2015 sample, but its forms are not ingested yet, and the 2024 rule change limits how far older evidence transfers. **C4** (NAV data) and **C5** (killed) gain nothing.

## Evidence

| # | Claim | Source | Tier | Key figure (quoted) | Grade |
|---|---|---|---|---|---|
| E1 | Alpaca daily bars start 2016-01-04; no plan sells earlier history | #104 probe (via history-extension E1); ADR 0009 option 6; Alpaca forum threads (S12, S13) | 1 (own probe) / 1 (own ADR) / 3 | probe: SPY and KO "the first on 2016-01-04. Nothing is returned for December 2015"; ADR 0009: Algo Trader Plus has "the same '7+ years' of history"; forum: "sip data is available from 2016" | SUPPORTED (as in history-extension E1; unchanged) |
| E2 | Sharadar Prices plan, Oct 2026: 5 yr USD 9/mo or 99/yr; 10 yr 19/199; **Full History USD 39/mo, USD 299/yr**; tables descriptions, tickers, actions, sp500, stocks, funds, metrics. Bundle full history 69/mo, 499/yr | S1 (sharadar.com/subscribe) | 1 | "Prices … Full History … $39 … $299"; "Bundle … Full History … $69 … $499"; "Subscriptions are subject to the Personal Use License Terms" | n/a (price fact; same as 2026-10-08 except the Prices annual figure is now read directly) |
| E3 | Sharadar licence: natural persons, personal use; delete within 30 days of termination; derived works that cannot reproduce the data may be kept; no sharing | S2 (sharadar.com/terms, s. 2, 4, 10); G8 S25 (2026-09-25); history-extension S22 (2026-10-08) | 1 | "solely to natural persons for personal use"; "Within thirty (30) days of termination, delete from all computer systems you own or operate all copies"; "You may keep research outputs, backtest results, models, summary statistics, trade logs, and similar derived works" that "do not contain and cannot reproduce the Services Data" | n/a (licence fact; stable across three reads) |
| E4 | Sharadar prices and actions from 1998, active and delisted | S3 (docs/actions); G8 S3 (QuantRocket: "1998 - present", "includes active and delisted tickers"); search summary of QuantRocket's data page (S9) | 1 (vendor) / 1 (reseller) / 3 | actions: "Deep history to 1998", "ticker changes, stock splits, cash dividends, spinoffs, … listing dates, delisting dates, delist reasons, acquisition counterparties" | **Claimed** (vendor and reseller only; no independent audit found, see Disconfirmation) |
| E5 | Sharadar `tickers` is the vendor's security master, with a permanent id, CUSIPs, a composite FIGI and an SEC-filings URL, but no CIK field | S4 (docs/tickers) | 1 | `permaticker`: "a unique and unchanging identifier for a security (share class)"; fields `cusips`, `figi`, `relatedtickers`, `firstpricedate`, `lastpricedate`, `isdelisted`, `secfilings`; the table "acts as the securities master for Sharadar datasets"; no `cik` field listed | INSUFFICIENT for the CIK join (one source; whether `secfilings` carries the CIK for every row is UNVERIFIED) |
| E6 | Sharadar's action `date` is not documented as an ex-date or an announcement date, and the docs say nothing on point-in-time | S3 | 1 | "The page doesn't say which date the `date` field represents" (fetch summary); G8: "Adjustments are applied on a backwards basis" | INSUFFICIENT (absence read from one page) |
| E7 | Sharadar `closeunadj` is raw and `closeadj` is adjusted for splits, dividends and spin-offs; whether `open`, `high`, `low` and `close` are split-adjusted is not confirmed here | G8 S16 (Sharadar SEP docs, 2026-09-25); `docs/prices` returned 404 today | 1 | G8: closeadj adjusted "for stock splits and cash dividends and spinoffs"; closeunadj is not | INSUFFICIENT for raw OHLC (one source; the OHLC basis is UNVERIFIED) |
| E8 | Norgate: Platinum "Back to 1990" with delisted names, USD 630/12 mo; Diamond "Back to 1950", USD 787.50; Gold and Silver have no delisted names; the updater is Windows only | S5 (norgatedata.com packages, today); G8 S21 (Windows only) | 1 / 1 | Platinum "$346.50" 6 mo, "$630.00" 12 mo; includes "OTC securities that were formerly major-exchange listed, and historical index constituents"; G8: "NDU will only work under Windows" | Prices **claimed**; Windows-only per vendor |
| E9 | EODHD: Historian USD 19.99/mo, USD 199/yr, "30+ yrs" EOD, dividends and splits; names delisted before 2018 have EOD only | S6 (eodhd.com/pricing, today); history-extension S25 (delisted page) | 1 / 1 | "$199.00/year"; delisted page: "Before 2018: EOD only" | **Claimed**; prices verified today |
| E10 | Tiingo: USD 30/mo or USD 300/yr for individuals, "30+ years", internal use only; delisted coverage only for tickers "that have not yet been recycled" | S7 (tiingo.com pricing, today); G8 S5 | 1 / 1 | "$30/month (or $300/year) for individuals"; "not yet been recycled" | Partial by the vendor's own word |
| E11 | Massive (formerly Polygon) Stocks Advanced: USD 199/mo, "20+ years", individual use only; corporate actions from 2008 | S8 (massive.com/pricing, today); G8 S13 | 1 / 1 | "$199/month"; "Individual use only"; G8: "back to 2008" | **Claimed**; actions depth falls short of 2000 |
| E12 | MarketParquet: a one-time USD 79 archive from 2000-01-03, "Survivorship-bias-free + delisted"; split-adjusted only; no corporate-action file, identifiers or upstream vendor stated | S10, S11 (marketparquet.com pricing and stocks pages) | 3 (marketing; unknown provenance) | "$79 once"; "8,500 delisted stocks within 16,000+"; "Split-adjusted OHLCV data"; licence allows "personal trading, research"; upstream "not stated" | INSUFFICIENT (Tier 3 only); fails the raw-bar and corporate-action needs as described |
| E13 | Stooq: no verifiable statement on delisted coverage | S9 (search); stooq.com/db/h returned empty | 3 | forum: "Free-ish: Stooq (bulk daily prices) … a bit messy"; one guide: assume survivorship bias unless delisted names are stated | INSUFFICIENT |
| E14 | Even complete databases miss the delisting return of names that leave for bad reasons, and the omitted returns are large | S14 Shumway (1997), *JF* 52(1), abstract; S15 Shumway and Warther (1999), *JF* 54(6), abstract | 1 / 1 | 1997: "correct delisting returns are missing for most stocks delisted for negative reasons since 1962", "the omitted delisting returns are large"; 1999: a corrected return of "−55 percent" for missing performance-related Nasdaq delistings | INSUFFICIENT by the count rule (two Tier 1 sources, same author), but on point: a vendor price series that ends at the last exchange session leaves out the same final return |
| E15 | A return run must read prices through the store's as-of API, never through an adapter | research-registry spec req 4 | 1 (own spec) | "read market and company data only through the as-of API … and never through an adapter" | n/a (our rule) |
| E16 | Today's store: filings from 2009, bars from 2016-01-04; delisted issuers are nearly absent from the bar store | C1 feasibility read; C1b feasibility read | 1 (own store reads, 2026-10-27) | C1: "1,301 CIKs that filed in 2017 and stopped filing by mid-2019, of which 4 have any bars"; C1b: "Filings reach back to 2009 in the store, but bars begin 2016-01-04"; "with prior" 24,402 (2015) and 20,711 to 23,355 a year (2016 to 2023); "with bars and shares" total 100,895 (2016 to 2023) | n/a (own measurement) |
| E17 | Spin-off counts vary a lot by year and definition; the pre-2016 rate is not higher than the recent one | S16 (J.P. Morgan), S17 (Treasury & Risk, citing Forbes), S18 (ETF Trends, citing Spin-Off Research), via search summaries | 2 / 2 / 3 | S&P 500 announcements "roughly 6 per year for 2000–2004 and 12 per year for 2005–2007"; "five in 2010 and only four in 2009" (major); "60 new companies were born via spins" (about 2014) | MIXED (definitions conflict) |

## 1. Candidate sources compared (back to 2000, delisted names, corporate actions)

Prices were read on 2026-10-27. "Claimed" means only the vendor says so.

| Source | Price (personal tier) | Depth | Delisted / survivorship-free | Splits, dividends, corporate actions | Licence for personal research | Format | Fit for our store |
|---|---|---|---|---|---|---|---|
| **Sharadar direct, Prices, Full History** | **USD 39/mo, USD 299/yr** (E2) | 1998 on (E4) | Claimed, active and delisted (E4) | `actions` from 1998 with ticker changes, splits, dividends, spin-offs and listing and delisting dates; one undocumented `date`, no announcement time (E4, E6); `closeunadj` raw (E7) | Personal use; delete within 30 days of termination; derived results may be kept (E3) | Bulk download (years=full) and API (E2) | Best: `permaticker`, CUSIPs and FIGI give a non-ticker key (E5); bulk files suit one backfill |
| Sharadar bundle, Full History | USD 69/mo, USD 499/yr (E2) | 1998 | as above | as above, plus as-reported fundamentals | as above | as above | Adds fundamentals EDGAR already covers from 2009 (history-extension E7); only matters before 2009 |
| Norgate Platinum | USD 630/yr (E8) | 1990 | Claimed; "essentially complete back to late 1992" (G8) | Four adjustment modes including raw (G8) | Personal; delete on expiry; two machines (G8) | Windows updater plus a Python package (E8, G8) | Needs a Windows VM on the owner's Mac |
| EODHD Historian | USD 199/yr (E9) | "30+ yrs" | Claimed; before 2018 **EOD only**, with no splits or dividends (E9) | After 2018 only, for delisted names | Personal; deletion clause unresolved (G8) | API, bulk by exchange-day (G8) | Delisted names before 2018 cannot be adjusted, and raw-plus-actions is how our store adjusts (ADR 0003 rule 1) |
| Tiingo Power | USD 300/yr (E10) | "30+ years" | Partial: excludes recycled tickers (E10) | Split factor and dividend per row, ex-date only (G8) | Internal use; delete on cancellation (G8) | REST only, no price bulk file (G8) | Recycled tickers are exactly the #787 and #1314 failure |
| Massive Stocks Advanced | USD 199/mo, 20% off annually (E11) | "20+ years" | Claimed; disputed by Tier 3 reports (G8) | Actions from 2008 only (E11) | Individual use; delete at termination (G8) | API, flat files | Price, and actions do not reach 2000 |
| Alpaca (any plan) | free / USD 99/mo | 2016-01-04 (E1) | Observed on samples (ADR 0009; #1311: 47 of 48 with `asof`) | Splits, dividends, spin-offs, name changes (ADR 0009) | Personal, no deletion clause (ADR 0009) | API | Cannot reach before 2016 |
| Stooq | free | not verified | not verified; assume survivor-only (E13) | not verified | not read | bulk files | Not usable as evidence |
| MarketParquet | USD 79 once (E12) | 2000 | Claimed (Tier 3) | Split-adjusted only; no actions file (E12) | Personal and research allowed; no deletion clause seen | per-day files | Fails raw-plus-actions and identity (tickers only); upstream unknown |
| CRSP | institutions only (G8) | 1925 | the academic standard | full | not sold to individuals | n/a | n/a |

## 2. Point-in-time fit

**What `known_at` a backfilled bar can honestly carry.** The store already stamps every bar at its session close, including bars fetched years later. The MTUM and RSP backfills say so ("Bars keep their session-close `known_at`", benchmark-backfill runbook step 3). A raw daily bar is a fact that existed at the close, so a vendor's raw bar can take the same stamp, with `source = 'sharadar'` in the existing `source` column (schema, `_common_fact_columns`). Three things need more than that:
- **Raw prices only.** Store `closeunadj` and, if `open`, `high`, `low` and `close` are split-adjusted (UNVERIFIED, E7), recover their raw values with the vendor's own split ratio. The ratio is a dated fact, so this introduces no look-ahead, but it needs a test. Never store `closeadj`. It is restated backwards (G8), and the store adjusts at read time (ADR 0003 rule 1).
- **Corporate actions.** Sharadar's actions carry one `date` and no announcement time (E6). The existing rule for Alpaca actions (#83: "stamps each action at the close before its ex-date", ADR 0009 context) carries over only if `date` is the ex-date. That needs checking against Alpaca's 2016-to-2025 actions in the overlap month (T144's pass bars on #1319).
- **Vendor revisions.** A vendor can correct history silently. A per-row `ingested_at` with the release name records which vintage a trial read, which the release record already does (data-releases runbook, "The record").

**How the release process takes it.** A vendor backfill writes rows known on or before every past trial's cutoff, so it stales every trial that reads those sessions. Each rerun counts in N (data-releases runbook, "Why"). It is therefore one named release: back up, `decision data-release open`, the backfill, `health --check`, `close` (T149b as written). The runbook also anticipates the licence: rule 7 says "If a data licence ever needs its data deleted …, the list of backups is the list of files to walk". With Sharadar, cancelling means deleting the vendor rows from the store **and from every backup taken after the backfill**, within 30 days (E3). Trials on those rows can then no longer be rerun, but their results may be kept. Paper windows are not touched, since the backfill writes only sessions before 2016 plus delisted tails, not open-window sessions (runbook rule 5). Fill-only (T147) keeps every Alpaca row as it is.

**Identity before 2016 is the larger risk.**
- ADR 0022 keeps CIK plus class as the key, with tickers from filings. Before mid-2019 the filings carry no ticker-exchange-title triple, and only about half carry a ticker at all (#1311 measurement). Before 2009 there is no XBRL.
- The safe join is vendor `permaticker` → CIK, through the `secfilings` URL (UNVERIFIED that it carries a CIK on every row) and CUSIPs, **never by ticker**. T146's line already says "by CIK and CUSIP, never by ticker". The store's own reuse cases (Q, VAL, FTI, BTU, SD) are the failure that rule prevents.
- Dual-class issuers (one CIK, several `permaticker`s) need the class rule. Before 2019 we have no class evidence of our own except the vendor's.
- Names whose CIK is not in the store's `securities` would need new master rows from a vendor snapshot. ADR 0022 point 4 forbids reading such rows back into the past (`snapshot_static` "is never evidence for a date before its `known_at`").
- For event studies keyed on a CIK that already has a filing (C1, C2's 10-12B), no listing history is needed: the event names the company. A universe ranked by size before 2019 does need T148, the paused listing-history work.

**Benchmarks (ADR 0005).** SPY's history is available through the vendor's funds table (SFP), which is in the Prices plan (E2). MTUM began trading in 2013 (benchmark-backfill runbook). A backtest trial refuses a run without every configured benchmark, so any **trial** window before 2013 needs an ADR 0005 decision on MTUM. Research runs such as C1 and C1b use SPY only, so this does not block them.

## 3. Effort and what it unlocks

**Plan-task size.** The shelved vendor lines in the data-foundation plan are the plan. This report's sizing reuses them, as a judgement rather than a measurement:

| Step | Who | Size | Notes |
|---|---|---|---|
| A new ADR re-opening ADR 0009's budget and amending ADR 0022 decision 1 and ADR 0023 D7 | owner (class B) | S | Says what happens to rows, backups and artefacts at cancellation (ADR 0009 Decision 5) |
| T144: one-month overlap test, pass bars written first (#1319) | owner buys; agent reads a store copy | S | USD 39 on the Prices plan (the line said USD 69 for the bundle) |
| T146: crosswalk `security_id` ↔ `permaticker` by CIK and CUSIP | agent | S | quant-auditor |
| T147: Sharadar `PriceSource` adapter, SEP and SFP bars and actions, fill-only, raw | agent | M | Raw-OHLC derivation and the action-date rule are the risky parts |
| T149: health counts for vendor-filled rows | agent | S | |
| T149b: the backfill on the real store under a release | owner | one sitting | Stales trials that read the touched sessions |
| T148: listing history (only for a pre-2019 *universe*) | agent | M | Not needed for C1 and C2; paused under ADR 0023 |
| T150: re-split on longer history (only for trials) | owner decision | S | Not needed for research runs |

Event-study minimum: ADR + T144 + T146 + T147 + T149 + T149b, about 3 agent tasks (S, M, S) and 2 owner sittings. A pre-2019 universe for backtests adds T148 and T150.

**What it unlocks** (estimates from repo counts; nothing below is a store read):
- **C1 / C1b (filing delay).** Filings are in the store from 2009, so the binding limit is bars. With vendor bars, traded events could start about 2010-07 (a year of cutoff history plus a 120-session beta), instead of 2016-07: 12.75 years instead of 7.25. At the 2015 rate of about 24,000 filings a year with a prior-year match (C1b table) and 60 to 80% coverage, that is about **70k to 100k more events before filters**, against today's 100,895 for 2016 to 2023. That roughly doubles a sample whose standard error is already 10 to 25 bp, so power is not C1's problem. The larger gain is **delisted names inside every year**. Today only 4 of 1,301 issuers that stopped filing in 2017 to 2019 have any bars (E16), which is why every C1 verdict reads "killed on survivor data". Going back before 2009 would need period ends from filing headers, which the store does not hold before 2015 by default (`header_first_year` defaults to `fsn_first_year = 2015`, config). That is new ingest work.
- **C2 (spin-offs).** The brief reports 8 to 12 events today. Spin-off activity before 2016 is not higher than after (E17). Scaling by years, from 2000 the total becomes roughly **25 to 50 events**, assuming the store's filing index holds 10-12B filings back to 2000 (`index_first_year = 1993`; not checked). For a 12-month hold with about 45% dispersion per event (an assumption, not measured), 40 events give a standard error of about 7 pp, so only a premium above about 14 pp a year would show at t = 2. C2 stays underpowered on its own children. Pre-2016 bars do enable the **CSD rung-2 read for 2007 to 2015**, which the sharper-candidates report proposed for 2007 to 2019 and which the store cannot run today. That read could also be done from a free total-return series (as the RSP read was), without a vendor.
- **C3 (activist 13D).** A 2001 to 2015 sample would cover the Brav et al. era, but the SC 13D forms are not ingested (S). Pre-2024 results also transfer only partly after the 2024 rule change (sharper-candidates C3).
- **C4 (CEF discounts).** Not unlocked: it needs NAV history, which no vendor here sells.
- **C5.** Killed at the desk; no change.
- **H1 and the factor families.** A pre-2019 universe needs T148 as well (#842: the universe was empty at 2017 to 2019). That is outside this brief.

## 4. Recommendation (evidence-graded options for the owner; not a decision)

**In plain words.** If you want history before 2016, the evidence points to one source: **Sharadar's Prices plan, full history, USD 39 a month or USD 299 a year.** It covers stocks and ETFs from 1998, includes dead companies (on Sharadar's word), and has the split, dividend and ticker-change records our store needs. It is the only licence read that lets you keep your results after you stop paying. The money is small. The real cost is three build tasks, two sittings of yours and one ADR. It also brings a rule you would be agreeing to: cancel, and the vendor rows go from the store and every backup within 30 days. Your own decisions today say "not yet": ADR 0022 keeps Sharadar out of the store, and ADR 0023 re-opens the question only for a strategy that has survived a harsh backtest (rung 4). C1 and C2 have not got that far. For C1, the bigger win would be the dead companies of 2016 to 2023, not the extra years. For C2, even 25 years of spin-offs stays a thin sample.

What the evidence supports, in order:
1. **For the source, if one is bought:** Sharadar direct, Prices, Full History (E2, E3, E4). Not the bundle (EDGAR covers fundamentals from 2009). Not Norgate (Windows, no clear edge after 1998), EODHD (no actions for delisted names before 2018), Tiingo (recycled tickers), Massive (price; actions from 2008) or the free sources (none verifiable).
2. **The cheapest decisive step is one month at USD 39** (T144). Ingest it on a store copy, check the pass bars on #1319 (identity, delisted completeness, raw closes against Alpaca for 2016 to 2023, action dates), and decide on a subscription from those numbers. Under the licence, derived statistics survive deletion.
3. **For C1 specifically,** a free step comes first. C1b's `n_dropped_no_entry_bar_*` counts (already in its design) measure how much of the sample survivorship removes. Alpaca serves delisted 2016 to 2023 bars when `asof` is set (#1311: 47 of 48), but ticker lookup is the limit. If those counts are large and C1b's spread is near a kill line, delisted bars are what would settle it.
4. **Under ADR 0023 D7 as written, nothing is bought** until a rung-4 survivor needs pre-2016 history. Changing that is an ADR.

**Owner steps this would need (none can be done by an agent):**
1. **A decision and an ADR (class B):** re-open ADR 0009's USD 0 budget, amend ADR 0022 decision 1 ("never becomes a store source") and ADR 0023 D7 (the rung-4 trigger), and set what happens to rows, backups and artefacts at cancellation.
2. **A paid account** at sharadar.com, as a natural person (E3), and **accepting the Personal Use License**.
3. **An API key in `.env`** (a new variable listed in `.env.example`, for example `SHARADAR_API_KEY`), or a bulk download done by hand.
4. **The backfill sitting** under a named release (T149b), accepting that it stales the trials that read those sessions.
5. **A check for existing access:** ADR 0022 refers to "whatever Sharadar access the owner holds". If you already subscribe, the purchase step is moot, but the ADR is not.
6. **If trials before 2013 are wanted:** an ADR 0005 decision on MTUM, which did not exist before 2013.

## Disconfirmation
- **Searches run** (the disconfirmation subset):
  1. "Sharadar SEP data errors missing delisted stocks bad prices complaints quantrocket forum"
  2. "Sharadar versus CRSP comparison coverage delisted returns accuracy"
  3. "Shumway 1997 The Delisting Bias in CRSP Data …" (whether vendor delisted series miss the final return)
  4. "Stooq … delisted survivorship bias" (whether a free source qualifies)
  5. "Alpaca market data historical bars how far back 2016 …" (whether any Alpaca plan reaches before 2016)
  6. "Norgate Data Updater macOS … Windows only" (whether the Windows limit still holds)
- **What was found against:**
  1. **No independent audit of Sharadar's delisted coverage exists in what was found.** One Tier 3 user reports "fairly complete coverage" back to 1998 by their own gap tests. One QuantRocket thread covers late daily delivery (operational, not history), and titles such as "Price not correct?" (2023) could not be read. Coverage stays **claimed**, and T144's overlap month is the only route to "verified".
  2. **Delisting returns.** Even CRSP lacked correct delisting returns for most negative-reason delistings, and the omitted returns are large (E14). A vendor series that stops at the last exchange session carries the same gap. Our Form 25 stamps tell when a name left, not its final return. Any C1 late-filer or failure-heavy read would need a delisting-return rule (for example, the Shumway-Warther −55% for performance delistings) declared before the run.
  3. **Alpaca:** nothing found that reaches before 2016 on any plan. The Tier 3 sources disagree on free-tier depth (2 or 5 years), which contradicts our own probe. The probe stands.
  4. **Stooq:** nothing verifiable either way. The search summary's guide says to assume survivorship bias absent a statement.
  5. **Norgate:** no evidence that the updater now runs on macOS. Old Python package metadata says Windows; current versions were not readable.
  6. **Spin-off density:** sources conflict on counts (E17). None suggests the pre-2016 rate is high enough to fix C2's power.

## Caveats & gaps
- **No store read was made** (no shell in this run). The C1 and C2 event gains are arithmetic from the experiment files' counts and the brief's 8-to-12 figure. The window behind "8 to 12 spin-off events" was not found in the repo.
- **Vendor coverage is claimed, not verified.** All prices were re-read today except Norgate's Windows limit and the licence details carried from G8.
- **Effort figures are judgements** from the shelved plan lines, not a sized plan. ADR 0022's context called the vendor migration "medium to large". This report's smaller event-study minimum assumes no pre-2019 universe (no T148).
- The 45% per-event dispersion behind the C2 power figure is an assumption, not a measurement.
- Sharadar's price-field definitions page returned 404, so the raw-OHLC question (E7) rests on G8's earlier read.

## UNVERIFIED items
- Whether Sharadar SEP `open`, `high`, `low` and `close` are split-adjusted (E7), and what `actions.date` means (E6).
- Whether every `tickers` row's `secfilings` URL carries a CIK (E5).
- Sharadar's delisted completeness against EDGAR Form 25 and Alpaca 2016 to 2023 (T144).
- Whether the store's filing index holds Form 10-12B back to 2000 (`index_first_year = 1993` in config; not counted).
- The window and filters behind C2's 8 to 12 events.
- Stooq's coverage; MarketParquet's upstream source, adjustment and licence beyond the sales page.
- Norgate's current macOS status.
- Spin-off counts per year before 2016 (E17 mixes definitions).

## Follow-up questions (not answered here)
- What delisting-return rule should an event study declare for names that leave for bad reasons (Shumway, Shumway-Warther)?
- Can a free total-return series for CSD (2007 to 2015) carry C2's rung-2 read without any vendor?
- How large are C1b's `n_dropped_no_entry_bar_*` counts once it runs, and do they decide whether delisted bars matter?
- Should 10-12B and SC 13D coverage before 2016 be counted in the store first, so C2 and C3's gains are measured rather than estimated?

## Sources
Seen on 2026-10-27 unless noted. "Local" means this repository.
- **S1** Sharadar, Subscribe (pricing): https://sharadar.com/subscribe (Tier 1)
- **S2** Sharadar, Terms of Use: https://sharadar.com/terms (Tier 1)
- **S3** Sharadar, Corporate actions documentation: https://sharadar.com/docs/actions (Tier 1, vendor doc)
- **S4** Sharadar, Tickers documentation: https://sharadar.com/docs/tickers (Tier 1, vendor doc)
- **S5** Norgate, US Stock Market Packages: https://norgatedata.com/stockmarketpackages.php (Tier 1)
- **S6** EODHD, Pricing: https://eodhd.com/pricing (Tier 1)
- **S7** Tiingo, Pricing: https://www.tiingo.com/about/pricing (Tier 1)
- **S8** Massive, Pricing: https://massive.com/pricing (Tier 1)
- **S9** Search summaries on Stooq and Sharadar coverage, including QuantRocket's data page https://quantrocket.com/data/ and https://docs-2-5--quantrocket.netlify.app/data, and a Reddit snapshot https://reddit.sentinel-team.org/posts/1s07rh1/snapshots/2026-03-27T19%3A24%3A11.814023Z (Tier 3); QuantRocket thread "Sharadar Pricing Data Not Updated" https://support.quantrocket.com/t/sharadar-pricing-data-not-updated/1157 (Tier 3)
- **S10** MarketParquet, Pricing: https://marketparquet.com/pricing (Tier 3, marketing)
- **S11** MarketParquet, Stocks data: https://marketparquet.com/data/stocks (Tier 3, marketing)
- **S12** Alpaca forum, "Alpaca Data API does not have data earlier than 2016?": https://forum.alpaca.markets/t/alpaca-data-api-does-not-have-data-earlier-than-2016/2332 (Tier 3; search summary)
- **S13** Alpaca forum, "Cannot get Historical Bar data before 2020": https://forum.alpaca.markets/t/cannot-get-historical-bar-data-before-2020/12415 (Tier 3; search summary)
- **S14** Shumway, T. (1997), "The Delisting Bias in CRSP Data", *Journal of Finance* 52(1): https://scholarsarchive.byu.edu/facpub/9278 (Tier 1; abstract via search summary)
- **S15** Shumway, T. and Warther, V. (1999), "The Delisting Bias in CRSP's Nasdaq Data and Its Implications for the Size Effect", *Journal of Finance* 54(6): https://ideas.repec.org/a/bla/jfinan/v54y1999i6p2361-2379.html (Tier 1; abstract via search summary)
- **S16** J.P. Morgan, spin-off report: https://www.jpmorgan.com/content/dam/jpm/cib/complex/content/investment-banking/archive/pdf-56.pdf (Tier 2; search summary)
- **S17** Treasury & Risk, "Corporate spinoffs surge" (2011): https://www.treasuryandrisk.com/2011/08/08/corporate-spinoffs-surge/ (Tier 2; search summary)
- **S18** ETF Trends, citing Spin-Off Research: https://www.etftrends.com/?p=163815 (Tier 3; search summary)
- **S19** Norgate Python package metadata: https://pypi.org/project/norgatedata/0.2.2/ (Tier 3, tooling; search summary)
- Local (Tier 1, own documents and measurements): [G8 price vendors](2026-09-25-price-vendors.md) (cited "G8 Sn"), [history extension and #1311 measurement](2026-10-08-history-extension.md), [sharper candidates](2026-10-27-sharper-candidates.md), [C1](../experiments/c1-filing-delay.md), [C1b](../experiments/c1b-filing-delay-matched.md), ADRs [0005](../decisions/0005-objective-benchmark-stop-criteria.md), [0009](../decisions/0009-price-vendor.md), [0016](../decisions/0016-development-boundary-and-forward-exams.md), [0022](../decisions/0022-no-vendor-security-master.md), [0023](../decisions/0023-break-first-fast-books-paper-exam.md), [charter](../charter.md), [data-foundation plan](../plans/data-foundation.md) T144 to T150 and `docs/work-map.toml`, [data-releases runbook](../runbooks/data-releases.md), [benchmark-backfill runbook](../runbooks/benchmark-backfill.md), `docs/specs/research-registry.md` req 4, `src/tradepartner/config.py` (`CalendarConfig.start = 1990-01-01`, `index_first_year = 1993`, `fsn_first_year = 2015`, `header_first_year`), `src/tradepartner/store/schema.py` (`_common_fact_columns`, `TABLE_PROVENANCE_VALUES`)
- Fetches that returned nothing usable (not counted as sources): https://sharadar.com/docs/prices (404), https://stooq.com/db/h/ (empty).

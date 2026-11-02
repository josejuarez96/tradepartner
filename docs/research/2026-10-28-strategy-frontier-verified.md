# Research Report: The overnight "strategy frontier" and store census, re-checked

**Brief:** #2525  ·  **Date:** 2026-10-28  ·  **Status:** COMPLETE for the questions in the issue. Published-paper figures are read from abstracts and secondary summaries, not full texts, and are marked UNVERIFIED where so.  ·  **Agent/model:** team `frontiercheck`, claude-sonnet-5-5. Store reads: read-only, `ops/store-copy.duckdb` only (a copy made 2026-10-28 01:40), by hand-written queries that live in the scratchpad and are not committed. No backtest, no return series, no trial registered. `.env` not read.

## Answer
**Verdict:** MIXED for the frontier as a ranking; NOT SUPPORTED for its headline "top 5 with +0.2 to +0.7 %/month net"  ·  **Confidence:** medium (the store counts are my own queries; the literature figures are abstract-level)

1. **The overnight work is a useful data map and a poor ranking.** Most store numbers hold up (8 statement facts, 2.37M rows, 1,772 delistings on file, 40 spin-offs, 9,523 initial 13Ds). The economics do not: the frontier takes the paper's best published spread (equal-weighted, every stock including micro-caps, 1960s-2000s), cuts it by the 58% post-publication rule, and subtracts a cost for one leg. Our universe is about 1,000 large and mid names with a $5 price floor and a $5M volume floor. Chen and Welch (2026) put the typical anomaly in non-micro stocks after 2005 at about 7 bp a month, before costs.
2. **Of the five "top" leads, one survives as a cheap, low-expectation desk read (asset growth), and four are dropped:** price delay and share volume work in the tiniest, least-traded stocks that our rules exclude; IntanCFP needs five years of price history that our bars (from 2016-01-04) cannot give until 2021; Piotroski needs net income, equity and debt, which the store does not hold (4 of the 9 tests are computable, not 8).
3. **Three claims in the census are wrong in ways that matter:** "about 7,000 firms a date" (it is 3,600 to 4,100 common stocks with an asset-growth signal, and 1,900 to 2,100 that pass our own price and volume rules, before the top-1,000 cap); "delisted coverage 77%" (true as a ratio, but the list it is measured against has only 36 delisters for 2016-07 to 2018, against 779 issuers in the earlier coverage probe, so it says nothing about 2016-2018); "SEC acceptance times are not available" (they are, for every 10-K and 10-Q with XBRL facts, in `statement_facts.known_at`; only the 8-K table `filing_events` is empty, by design).
4. **The fill audit's "22.2 bp matches the backtest" is not supported.** Its time-of-fill table is a programming error (every fill lands in the "0 to 5 seconds" row), it weights multi-fill orders by their fill count, and it averages buys and sells whose overnight-gap terms have opposite signs. Done per order: buys paid +46.9 bp against the open (95% interval +37.7 to +57.2), sells +9.5 bp (+1.6 to +19.9). That is above the 15 bp per side the backtest charges, and it grows as liquidity falls.
5. **13D passes its own feasibility check only on a generous reading.** Of 9,523 initial 13Ds, 4,168 have a usable entry bar, but only 1,430 (873 names) pass our price and volume rules; the median target trades about $2M a day. The literature says the drift after the filing, as opposed to around it, is weaker than before it. I rate it FIX, not pass.

Hence: **1 KEEP, 4 FIX, 9 DROP** (table below). Nothing here changes what the owner has already decided; it narrows what is worth a first look.

## Lead-by-lead table

KEEP = worth a cheap next step; FIX = the idea may be fine but the claim or the check is wrong and must be redone; DROP = do not spend a look on it now. "Verified" means my own number or reading. All monthly figures are percent per month.

| # | Lead | Frontier's claim | Verified value | Verdict |
|---|---|---|---|---|
| 1 | Asset growth (Cooper, Gulen, Schill 2008) | Ready; +0.68 net; ~7,000 firms per date | Arithmetic of the gross cut is right (1.73 x 0.42 = 0.73), but 1.73 is the equal-weighted, all-stock, long-short spread, 1968-2003. The same paper's value-weighted spread is 8.4% a year, 0.70 a month, which after the cut is 0.29. Chen-Welch prior for non-micro, post-2005: 0.07. Costs for two legs plus borrow: 0.05 to 0.35. Net: about -0.3 to +0.2, centred near zero. Firms per date: 3,600 to 4,100 common (1,900 to 2,100 liquid), not 7,000. Data is the strongest in the store (`total_assets`, as filed, `known_at` set) | **KEEP** as a desk read with prior near zero |
| 2 | Price delay slope (Hou, Moskowitz 2005) | Ready; +0.46 net | Arithmetic right (1.21 x 0.42 = 0.51), but the premium sits in a sliver of the smallest, most delayed stocks (the paper's most-delayed group is 0.02% of market value); Chen-Zimmermann rate the replication "3_distant" and predictability "2_likely" (the weakest tier in the file); our rules remove those stocks | **DROP** |
| 3 | Share volume (Datar, Naik, Radcliffe 1998) | Ready; "t = 8.86 suggests strong" | It is share turnover (volume / shares outstanding), not dollar volume. The source sample is NYSE stocks only, 1962-1991, no return figure in the catalogue, so there is no effect to compare with cost. Low-turnover stocks are the illiquid ones our $5M rule drops. B10 already uses turnover | **DROP** |
| 4 | IntanCFP (Daniel, Titman 2006) | Feasible with OCF and market cap; 12 sorts a year | The signal regresses a stock's five-year past return on a five-year-lagged cash-flow-to-price. Bars start 2016-01-04, so the first computable month is 2021-01 (36 months to the boundary). The frontier's proxy (operating cash flow over market cap) is not the paper's measure (income plus depreciation). No published return | **DROP** |
| 5 | Piotroski F-score (2000) | Feasible; 8 of 9 parts; +0.22 net | Computable parts: 4 of 9 (operating cash flow positive, equity issuance from the cover-page share count, change in gross margin, change in asset turnover). Net income, long-term debt, current assets and liabilities, and book equity are not in the store. The paper's sample is the highest book-to-market fifth, concentrated in small, low-turnover, unfollowed firms. The cost line used (0.60 a month) assumes a full turnover every month, which no fundamentals signal has (B3 measured 10.4% one-sided) | **DROP** (data-blocked; also the retired quality line) |
| 6 | Mohanram G-score | Feasible, marginal | Needs earnings variance, R&D, capex, advertising, net income: mostly not held | **DROP** |
| 7 | Debt issuance, EntMult, abnormal accruals, R&D ability, institutional ownership x B/M | Data-blocked | Confirmed: no debt, EBITDA, net income, R&D, patents or 13F in the store. Their "net" figures are stated "if data existed" and use the one-leg cost | **DROP** (blocked verdict stands) |
| 8 | Dividend seasonality | Economically unpromising | Agree. 0.36 x 0.42 = 0.15 a month against a full monthly turnover; about 9,200 to 10,500 dividend rows a year held | **DROP** |
| 9 | Spin-offs / spin-off children (C2) | Underpowered | Agree. 40 spin-off rows 2016-07 to 2023-12, on 37 dates, and none before 2020-11-17. Already deferred as C2 | **DROP** (no change) |
| 10 | XBRL earnings surprise | Feasible with qualifications | The only timestamp in the store is the 10-Q/10-K acceptance, a median 43 to 45 days after period end, which is after the earnings announcement (the 8-K 2.02) that B9 already tested and killed (TP-6). Trading on the later date trades after the move | **FIX** (reframe as part of the B9 line or drop) |
| 11 | Store census (fund and filings) | 8 facts; filing_events empty; 77% delisted coverage | See "Census re-derived". Counts hold; three conclusions drawn from them do not | **FIX** |
| 12 | Paper fill audit | 49.8 bp vs open; 22.2 bp vs prior close "matches the backtest" | See "Fill audit". Per order, buys +46.9 bp, sells +9.5 bp vs the open; the summary's own conclusion is unsupported | **FIX** |
| 13 | Activist 13D (C3) feasibility | Verdict "pass" | Counts reproduce (9,523 initial 13Ds, 4,233 mapped target CIKs). Tradable under our rules: 1,430 events, 873 names. Power check uses raw counts. Cost line (60 bp) is far below what $2M-a-day names cost. The drift after filing is weakly supported | **FIX** (rerun the check on the tradable subset; do not build the ingest for this alone) |
| 14 | Idea queue (top 20 of 117) | Ranked list, known errors | Built from the Chen-Zimmermann catalogue, whose "return" is the paper's own best long-short spread. Its "HELD" tag is wrong for Piotroski, accruals, EntMult and others. Do not use the ranking | **DROP** |

## Census re-derived

Source: `statement_facts`, `prices_daily`, `delistings`, `securities`, `classifications`, `filing_events`, `corporate_actions`. Numbers that differ from the overnight files are in bold.

| Item | Overnight file | My query | Note |
|---|---|---|---|
| `statement_facts` rows, fact names, CIKs | 2,373,740; 8; 15,164 | 2,373,740; 8; 15,164 | Same. Facts: revenue, gross_profit, cost_of_revenue, total_assets, operating_cash_flow, net_interest_income, noninterest_income, revenue_financial. No net income, equity, debt, current items, EBITDA, R&D |
| Rows by fact | as listed | Same | |
| `total_assets` | 408,042 rows | 408,042; all with `period_days = 0` (a balance-sheet point) | Census item (e), "0 company-quarters with revenue, cash flow and assets", is an artifact: assets are never in a "quarter" bucket, so the join is empty by construction. It does not mean the three cannot be combined |
| Annual (10-K) filers FY2020: assets / cash flow / revenue / gross profit | not given | 5,665 / 5,431 / 4,487 / 2,769 | |
| Gross profit missing | "about 23% (3,562 of 5,560 in 2020)" | **38% of annual revenue filers (2,769 of 4,487); 37% of quarterly (3,370 of 5,307)** | Includes banks and insurers, which have no cost of goods. The overnight census's own table gives 3,388 of 5,307 in 2020, which is also about 36% missing, not 23% |
| Quarterly revenue filers 2020 | 5,307 | 5,307 | Same |
| Statement facts first-vintage only, `provenance = filing` | not stated | all rows `edgar`/`filing`; median lag from period end to `known_at` 41 to 45 days | Point-in-time as filed; restatements are not reflected (by design, data-foundation #660) |
| `filing_events` | empty | 0 rows | True, and expected: the table holds 8-Ks only and its ingest is off by default (`edgar.filing_events_enabled = false`). Wrong conclusion in the frontier: acceptance times for periodic filings are available through `statement_facts.known_at` and `filing_accession` |
| Securities with bars, whole history | not given | 8,860 (of 63,557 securities rows) | |
| Common-stock securities with a price on 2020-08-31 / 2021-12-31 / 2023-12-29 | "about 5,900 to 7,000 a date" | **4,062 / 4,745 / 4,492** (all share classes with a bar: 4,911 / 6,030 / 5,684) | |
| Of those, price at least $5 and 20-day median dollar volume at least $5M | not given | 2,046 / 2,420 / 2,108 | The real universe then takes the top 1,000 by market cap |
| Common with an asset-growth signal (two consecutive 10-K assets, known by the date, last 15 months) | "about 7,000" | **3,607 / 3,733 / 4,111; liquid subset 1,879 / 2,061 / 2,003** | |
| Delisters on file with effective date at or before 2023-12-29 | 1,769, of which 1,363 have bars (77%) | 1,772 (1,365 with bars, 77.0%); 1,098 common (1,065 with bars) | Ratio reproduces. The denominator does not measure coverage: see next row |
| Delisters on file by year (2016-07 to 2023): all classes (of which common, common with bars) | "86% for 2016-18 (43 of 50)" | 2016: 5 (5, 4); 2017: 18 (15, 15); 2018: 13 (10, 8); 2019: 72 (63, 61); 2020: 214 (158, 151); 2021: 342 (196, 190); 2022: 427 (231, 221); 2023: 599 (346, 344) | The list is thin before 2019: 36 delisters for 2016-07 to 2018, against the 779 issuers in the owner's earlier coverage probe (about 310 a year). 2019 (72) is also far under that rate. Securities whose last bar falls in 2016, 2017, 2018 are 4, 3, 3; in 2019 to 2023 they are 58, 192, 327, 482, 669. So any strategy read before 2020 is on survivors, and the "77%" is circular |
| Spin-off rows | 40 on 37 dates | 40 on 37 dates (2020: 1, 2021: 7, 2022: 11, 2023: 21) | Same |
| Dividend rows | "10k+ a year" | 9,158 (2020), 9,792, 10,248, 10,499 (2023) | Same |
| Universe note | "securities with prices in 2023 (6,489)" | not re-derived | The relevant universe is ADR 0006's top 1,000, not any proxy |

## Fill audit re-derived (3 sessions, 593 orders, 918 fills)

Method: one row per order (VWAP of its non-superseded fills), latest-revision bars, buy-adverse-positive, order-level 95% bootstrap intervals. For 2026-10-20 buys it reproduces the earlier [10-25 audit](2026-10-25-paper-fill-costs.md) exactly: gap -52.4 bp, residual +52.7 bp, total 0.0 bp.

| Cut | Overnight gap (adverse +) | Fill vs open | Fill vs prior close |
|---|---|---|---|
| All buys (396) | -5.8 | **+46.9** (37.7 to 57.2) | +40.8 (25.4 to 55.9) |
| All sells (197) | -64.5 | **+9.5** (1.6 to 19.9); median 0 | -54.8 |
| All orders pooled | -25.3 | +34.5 | +9.1 |
| Overnight gap by session, raw | 10-20: -52; 10-26: +49; 10-27: +59 | | |
| Buys by 20-day median dollar volume: under $50M (25) / $50-200M (197) / over $200M (174) | | **+131 / +51 / +30** | |
| Sells by the same bands | | +52 (15) / +6 / +6 | |

What the data supports:
- **The overnight gap is the market's move on three days, not a cost.** The three sessions gapped -52, +49 and +59 bp on average. Averaging them with buys and sells together gives a number (22.2 bp in the overnight summary, 9.1 bp per order here) that depends on which three days were sampled and how many buys and sells there were. It cannot "match" anything. The same applies to the summary's "1.8 bp gap".
- **The execution term is the fill versus the open.** Buys cost about 47 bp (interval 38 to 57), above the 15 bp per side the backtest charges, and the cost rises as liquidity falls (30, 51, 131 bp). Sells fill at the open itself in most cases (179 of 197 within five seconds, median 0 bp). Buys fill later: 50 of 396 within five seconds (about 0 bp), the rest median 35 s (10-20) to 454 to 549 s (10-26, 10-27), at a mean of +41 to +73 bp.
- **The summary's time table is a code error.** `audit.py` multiplies the hour (a small integer type in polars) by 3,600 and wraps around, so "seconds after open" is about -1,750 for every fill and all 918 fills fall in the "0-5s" row. Its "median 3.5 fills per order" line also disagrees with its own table (mean 2.2).
- **Weighting.** The audit averages over fills, so orders split into 4 pieces count 4 times. Fill-weighted pooled residual is 49.4 bp; order-level is 34.5 bp. The 10-20 session is 202 buys of $494 each; the 10-26 and 10-27 orders are mostly $1 to $50, where cost in dollars is trivial and the sample is not the production size.
- **Still true, as the audit says:** these are paper fills matched to the quote by the broker's simulator. They do not measure live slippage. The buy-versus-sell asymmetry, 47 against 9.5 bp, is the simulator's timing (sells released at the open, buys minutes later), not a market feature.

Not re-checked: rejected/cancelled orders ("none"). The summary's "1% partially filled" is wrong against my count: 175 of 593 orders (30%) have two or more fills.

## Economics, redone with consistent units

All monthly, percent per dollar of each leg (a 1-dollar long and 1-dollar short book); one-sided annual turnover of 100% per leg for an annual sort (an assumption, UNVERIFIED; no turnover was measured).

- **Gross:** published spread x 0.42 (the 58% post-publication cut, QI-2). Alternative prior: Chen and Welch's 7 bp for non-micro, post-2005 (G2-S10, abstract only).
- **Cost, two legs:** 2 legs x 100% x 2 sides x c / 12. At the backtest default c = 15 bp: 5 bp a month. At the 30 bp the ledger uses for small names: 10 bp. The overnight file charged one leg at 30 bp (5 bp a month).
- **Borrow on the short leg:** the cost config's own ladder is 0, 30, 300 bp a year (`costs.borrow_sensitivity_bps`): 0, 2.5 and 25 bp a month on short value.
- **Power (4a, item c):** a monthly long-short series has about 40 non-overlapping months in 2020-08-31 to 2023-12-29. Months are not overlapping returns even when membership persists, so n is about 40, not the 3.3 annual sorts. With an assumed monthly long-short volatility of 2% for large names (UNVERIFIED), the effect for expected t = 1 is 2 / sqrt(40) = 0.32 a month. Asset growth's value-weighted haircut value (0.29) falls just short of it (expected t about 0.9 over 40 months). The non-micro prior (0.07) needs about 800 months.

| Lead | Published spread | After 58% cut | Non-micro prior | Cost + borrow | Net range |
|---|---|---|---|---|---|
| Asset growth, equal-weighted all stocks | 1.73 | 0.73 | 0.07 | 0.05 to 0.35 | +0.38 to +0.68 (not tradable: micro-caps) |
| Asset growth, value-weighted | 0.70 | 0.29 | 0.07 | 0.05 to 0.35 | -0.06 to +0.24 |
| Price delay | 1.21 | 0.51 | 0.07 | 0.05 to 0.35 | micro-cap only |
| Piotroski | 1.96 (high B/M, long-short) | 0.82 | 0.07 | monthly turnover ~10%: 0.06 to 0.12 (borrow not included) | data-blocked |

The overnight file's +0.68 for asset growth is therefore the first row with the cost of one leg: right as arithmetic, wrong for our universe by a factor of three to ten.

## Evidence
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Asset-growth spread, 1968-2003: equal-weighted 19.5% a year, value-weighted 8.4% | Cooper, Gulen, Schill, JF 2008, via search summaries; Chen-Zimmermann catalogue (return 1.73 a month, t 8.45, equal-weighted, deciles) | Tier 1 / catalogue; abstract and summary level, UNVERIFIED in full text | 19.5% and 8.4% a year | No | No |
| A follow-up argues the asset-growth result is largely an omitted-delisting-return artifact | Summary found in the same search; paper not named or read | Tier 3, UNVERIFIED | "disappears once delisting returns are included" | Yes | No |
| Non-micro, post-2005 median anomaly return is 7 bp a month, and modest costs remove it | Chen and Welch, arXiv 2607.06502 (abstract) | Tier 2 (preprint, abstract only) | 48 bp to 19 bp (post-2005) to 26 bp (non-micro) to 7 bp (both) | Yes | Costs would remove it, per the authors |
| Net of trading costs and post-publication effects the average anomaly earns 4 bp a month; post-2005 decay 72% | Chen and Velikov, JFQA 2023 (abstract; earlier drafts 8 and 13 bp) | Tier 1 (abstract) | 4 bp a month | Yes | Yes |
| 65% of 452 anomalies fail |t| >= 1.96 with NYSE breakpoints and value weights | Hou, Xue, Zhang, RFS 2020 (abstract) | Tier 1 | 65%; 96% in the trading-frictions group | Partly | No |
| Price-delay premium concentrated in a very small segment; later work attributes it to liquidity risk | Hou, Moskowitz, RFS 2005; Lin et al., J. Financial Markets 2014 (abstracts) | Tier 1 (abstracts) | "0.02% of the market" | Pre-2001 sample | No |
| Piotroski's benefits are concentrated in small and medium firms, low share turnover, no analyst coverage; sample is the top book-to-market fifth | Piotroski, JAR 2000, via a quoted summary | Tier 2 (secondary), UNVERIFIED | 14,043 firm-years; 23% a year long-short | No (1976-1996) | No |
| Datar-Naik-Radcliffe sample is NYSE stocks, 1962-1991, no return in the catalogue | Chen-Zimmermann catalogue row (`Filter exchcd==1`) | Tier 2 | t 8.86 | No | No |
| Daniel-Titman signal is a regression on a five-year past return; no return in catalogue | Catalogue row (`Return` blank) | Tier 2 | t 4.89 | No | No |
| 13D filing-window return about 7% over [-20, +20]; post-filing lower than pre-filing | Brav et al. JF 2008; IJBAF 2023 (both as read in the [sharper-candidates report](2026-10-27-sharper-candidates.md), not re-read here) | Tier 1 (abstract) | 7%; "significantly lower" | Mixed | No |
| Store facts (counts above) | Own queries on the store copy | Own result | See census | n/a | n/a |
| Fill costs (table above) | Own queries on the store copy, 3 sessions | Own result | See fill audit | Paper only | n/a |

## Disconfirmation
- **Searches run:** (1) Chen-Zimmermann post-publication decay for asset growth, value-weighted and large stocks; (2) Hou-Xue-Zhang replication and microcap treatment; (3) Chen-Welch 2026 on non-micro post-2005 returns; (4) Chen-Velikov net-of-cost figure; (5) Piotroski sample and where the return sits; (6) Cooper-Gulen-Schill spreads; (7) Hou-Moskowitz delay premium and post-publication work. No search found an asset-growth post-publication figure for large stocks.
- **What was found against my own conclusions:** (a) Chen and Zimmermann report that publication bias in 156 portfolios is only about 12% of in-sample return, much smaller than post-publication decay, which supports the view that these spreads were real mispricing, so a prior of zero may be too harsh. (b) A critique of Hou-Xue-Zhang finds a replication rate of 82% on alpha tests, far above their pessimistic number. (c) The repo's own Q1 note names Jensen, Kelly and Pedersen (JF 2023), who argue published factors replicate better than multiple-testing cuts imply. (d) Asset growth has a value-weighted spread that stays positive and significant (8.4% a year, t about 5): it is not a micro-cap-only effect. This is the reason asset growth is KEEP and not DROP. (e) Our store lacks most delisters through 2019, so a read on 2016-2018 data is on survivors, which flatters a long leg and denies a short leg the gains from stocks that failed. Any store test should start no earlier than 2020-08-31 (the practice window) or say so.
- **What I did not do:** read any full text; query a source for the post-2010 asset-growth long leg; compute any return or turnover in the store.

## Caveats & gaps
- Store numbers describe the 2026-10-28 copy. `prices_daily` holds revisions (4,803 security-sessions have more than one row since 2026-10-15); I took the latest `known_at`.
- Liquidity filters here use close x volume on bar data, the same definition as ADR 0006 rule 5, but the true universe also applies the exchange, common-stock and market-cap rules; the 1,900 to 2,100 figures are an upper bound for that step.
- The 13D target-to-security mapping joins on CIK. Securities with several share classes produce more than one match; I counted distinct filings.
- Turnover, volatility and borrow inputs are assumptions, each marked UNVERIFIED.

## UNVERIFIED items
- Every published-paper figure in the Evidence table (abstract or secondary reads).
- Asset growth's one-sided annual turnover (100% assumed).
- Large-name monthly long-short volatility (2% assumed).
- Whether the "delisting return" critique of asset growth is a real, citable paper.
- The 13D "post-filing lower than pre-filing" result beyond its abstract.
- The Piotroski concentration quote (secondary).

## Follow-up questions (not answered here)
1. **For the owner:** is a long-short asset-growth read worth a desk look given that the long-only leg is the cheaper claim and the premium sits mostly in the short leg (LS-6, LS-20)? The backlog entry offers both legs, long leg first.
2. **For the owner:** should the store add the missing XBRL facts (net income, equity, long-term debt, current assets and liabilities, depreciation)? It would unblock Piotroski, Mohanram, accruals, net operating assets, enterprise multiple and debt issuance at once. With a prior near zero for all of them, I would not do it for this reason alone.
3. Should `docs/ways-of-working/research-program.md` item 4a(f) say how to count delisters (use the owner's earlier coverage probe, not the `delistings` table)? The 77% figure in the overnight work shows the trap.
4. Should the fill audit script be fixed and rerun by the team that owns paper slippage (Int8 overflow, per-fill weighting, sign handling)? The earlier 10-25 report remains the better record.
5. Does the 13D filer list (activist or not) exist anywhere before 2024-12? Without it the activist share of the 9,523 filings is unknown.

## Next tests, in plain words
1. **Read a free published series, no store run.** Chen and Zimmermann publish the long and short leg of asset growth for large stocks; read the 2010-2019 value-weighted, size-screened legs and see whether either exceeds 10 bp a month. Cheap, and it also decides the whole "investment and share issuance" family together with Q1.
2. **Finish what is already queued.** Keep Q1 (net share issuance) and B10 ahead of anything new here; nothing in this frontier beats their information value.
3. **Repair the fill audit and rerun it by order**, so the next cost decision uses 47 bp for buys and 9.5 bp for sells (paper-simulated) and the liquidity gradient, not 22 bp.

## Sources
- Overnight files: `~/tradepartner-probes/frontier/frontier.md`, `idea-queue/top20.md`, `idea-queue/candidates.csv`, `idea-queue/raw/SignalDoc.csv`, `census/fund/census.md`, `census/filings/census.md`, `census/fills/report.md`, `census/fills/audit.py`, `c3-13d/feasibility.md`, `c3-13d/sc13d.csv`.
- Repo: [research program](../ways-of-working/research-program.md), [hypothesis backlog](hypothesis-backlog.md), [sharper candidates](2026-10-27-sharper-candidates.md), [paper fill costs](2026-10-25-paper-fill-costs.md), [Q1 note](2026-10-19-q1-repurchasers-rung12.md), `src/tradepartner/config.py` (`CostsConfig`, `UniverseConfig`, `ExecutionConfig`).
- Chen and Welch, "What Useful Alphas?", arXiv 2607.06502 (abstract): https://arxiv.org/abs/2607.06502
- Chen and Velikov, "Zeroing in on the expected returns of anomalies", JFQA 2023: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225
- Hou, Xue, Zhang, "Replicating anomalies", RFS 2020: https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html
- Cooper, Gulen, Schill 2008 (summaries): https://cxoadvisory.com/fundamental-valuation/asset-growth-cross-section-stock-returns
- Hou and Moskowitz 2005: https://econpapers.repec.org/RePEc:oup:rfinst:v:18:y:2005:i:3:p:981-1020
- Piotroski 2000: https://econpapers.repec.org/RePEc:bla:joares:v:38:y:2000:i::p:1-41 ; summary quote: https://www.gurufocus.com/news/459139/the-piotroski-fscore-does-it-have-meaningful-applications
- Chen and Zimmermann, "Open source cross-sectional asset pricing", Critical Finance Review 2022: https://www.emerald.com/cfr/article/11/2/207/1324917/Open-Source-Cross-Sectional-Asset-Pricing

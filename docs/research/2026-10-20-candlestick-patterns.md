# Research Report: daily candlestick-pattern signals on US equities, net of costs

**Brief:** #1944  ·  **Date:** 2026-10-20 (revision 2, after the owner's review of revision 1)  ·  **Status:** COMPLETE (about 28 searches and 10 failed or blocked fetches; 7 full texts read, 14 abstracts or summaries; source log at the end)  ·  **Agent/model:** researcher (team candlebrief), Claude Opus 5.5

**Decision this unblocks:** whether a daily candlestick hypothesis earns a backlog item and a signal build (a signal reading daily open, high, low and close, which no current signal does), or goes on the parked list.

**Scope (revision 2).** [ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md) (accepted 2026-10-19) applies: short and long/short positions are in scope for research, backtests and paper (live stays long-only); universe size is a per-hypothesis choice (the store has daily bars for about 5,573 names); any time frame is in scope for research. Revision 1 scored only the long side and the top 1,000; the owner rejected it for that. This revision scores bearish patterns and short legs on the same footing as bullish ones, prices short-side costs, and reads the pattern-by-pattern tables of the main positive study.

**Terms used once.** A *candlestick* is one day's open, high, low and close drawn as a box (the *body*, open to close; white if the close is above the open, black if below) with lines (*shadows*) to the high and low. A *pattern* is a named shape of one to three such days: a *hammer* (small body, long lower shadow), a *doji* (open about equal to close), an *engulfing* day (today's body covers yesterday's), a *harami* (today's body sits inside yesterday's), a *morning star* (a long black day, a small day, then a white day closing well into the first day's body) and its mirror the *evening star*, *three white soldiers* and *three black crows* (three rising white, or three falling black, days), *three inside up/down* and *three outside up/down* (a harami or engulfing pair plus a confirming third day). *Bullish* patterns are buy signals; *bearish* patterns are sell or short signals. A *reversal* pattern is meant to end a short trend, so studies first define one (for example, "the 3-day average fell on most of the last 6 days"). *Exit rules:* the *Caginalp-Laurent (CL)* exit sells a third of the position at each of the next three closes and scores the average; the *Marshall-Young-Rose (MYR)* exit sells everything on one fixed day. *Data snooping* is finding a rule that worked by chance because many rules were tried; *Step-SPA* and *SSPA* are tests of whether the best of many tried rules beats chance; the *deflated Sharpe* does the same for a Sharpe ratio, given the number of trials. *Borrow fee:* the annual rate a short seller pays to borrow the shares; *easy-to-borrow (ETB)* names cost little or nothing, *hard-to-borrow (HTB)* names can cost tens of percent a year.

## Answer

**Verdict:** MIXED  ·  **Confidence:** medium

**What the evidence says.** Named daily candlestick patterns carry at most a small, pattern-specific signal in US stocks, and only in old, survivor-only samples of large stocks. The careful negative test (Marshall, Young and Rose 2006: 28 signals, Dow stocks 1992-2002, bootstrap) found nothing better than chance. Read pattern by pattern with a fixed three-day exit, the main positive study (Lu, Chen and Hsu 2015) shows six of its eight patterns at or below zero before costs. Only the morning star (long) and the evening star (short) show a gross edge: about 0.5% per trade on 26 surviving Dow stocks and about 1.0% on 19 surviving NASDAQ-100 stocks, 1992-2012. The short side is no weaker than the long side. The large positive figures in the literature come from the averaged CL exit. Its original form buys at a price no order can get (Caginalp and Laurent 1998), and Lu, Chen and Hsu's CL tables cannot be reconciled with their own fixed-exit tables (§1). The one modern study, Jiang, Kelly and Xiu (*Journal of Finance* 2023), uses a neural network on daily OHLC charts of all CRSP stocks, not named patterns. It finds strong gross prediction, out of sample over 2001-2019, but value-weighted much weaker than equal-weighted, turnover like short-term reversal's, and no costs. No post-2012 study of named patterns on US stocks, net of costs, was found.

**What our cost model implies (separate from the evidence).** At 15 bp per side a round trip costs 30 bp; a 3-day short adds about 0.4 bp of borrow at 30 bp a year, 3.6 bp at 300 bp a year, and about 36 bp on a name borrowing at 30% a year. Against that, the published gross per trade, cut by the usual post-publication decline (QI-2, about 58%), is 0.1-0.25% for all eight patterns pooled and about 0.23% (Dow) to 0.45% (NASDAQ-100) for the two stars. Before the cut, only the NASDAQ-100 stars (1.06%) clear a 3x-cost line of 90 bp. After it, nothing does, and only the NASDAQ-100 stars clear break-even. So named patterns fail rung 1 of the [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) kill ladder on published figures. That is a finding about edge size against cost, not about scope.

**Proposal (the owner decides).** Park standalone named-pattern trading on this evidence (parked row below). Ledger row L-1 is updated. If the owner wants the one test that could change this, §4 gives it: stars long and short against a matched short-term-reversal control, two trials. The learned-OHLC thread (Jiang, Kelly and Xiu) is a different and stronger hypothesis. It falls under the ML validation gates, so it is a follow-up brief, not this one.

## The four questions

### 1. What the evidence says, after realistic costs

**US, daily, named patterns**

| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Three-day reversals predict a 2-day bounce, both directions | Caginalp & Laurent (1998), *Applied Mathematical Finance* 5, 181-205 (full text) | 1 | S&P 500 stocks 1992-1996: the four bullish patterns average 0.9% per trade (long) and the four bearish 0.27% (short), p. 200; about five buy and eight sell signals a day | Definitions from a practitioner book, data 1992-1996; no later period | Estimated only: 0.56-0.76% per trade after an assumed 0.1-0.3% spread (p. 201). **Buys at the close that completes the pattern**, a price no order can get, which also picks up the bid-ask bounce |
| No candlestick signal beats chance in large US stocks | Marshall, Young & Rose (2006), *J. Banking & Finance* 30, 2303-2323 (authors' companion preprint, full text) | 1 | 14 single lines and 14 patterns, bullish and bearish; 2-, 5-, 10-day holds "very similar"; 500 bootstrap series per rule; the bullish Opening White Marubozu predicts a significantly *negative* return | DJIA stocks 1992-2002 | n/a, no gross edge |
| Same conclusion on 349 US stocks | Horton (2009), *Quarterly Rev. Economics & Finance* 49(2), 283-294 | 1 (read only through two theses) | Eight patterns on 349 S&P 500 stocks; Kolmogorov-Smirnov, Cramer-von Mises and Birnbaum-Hall tests; returns after candlesticks no better than "choosing at random" (Mukansi thesis §2.4.1; Jönsson 2016 §1.4) | Not known | Not known |
| With a fixed 3-day exit, six of eight reversal patterns have no gross edge; morning and evening stars do | Lu, Chen & Hsu (2015), *J. Banking & Finance* 61, 172-183 (IEAS WP 14-A010, July 2015, full text; Tables 4, 5, 7) | 1 | Gross per trade = net + their 0.5% round trip, EMA10 trend, next-open entry, exit at the day-3 close. **Dow survivors:** long TWS -0.35%, TIU -0.17%, TOU -0.15%, MS +0.47%; short TBC +0.14%, TID -0.05%, TOD -0.21%, ES +0.63%. **NASDAQ-100 survivors:** long +0.36, -0.30, -0.16, +1.01; short +0.60, +0.49, -0.31, +1.11. Trade-weighted: Dow 0.23% (long 0.15, short 0.29), NASDAQ 0.56% (long 0.46, short 0.65); stars 0.56% and 1.06% | 1992-2012; 26 of 30 Dow and 19 NASDAQ-100 stocks, **survivors only** (§3.1, §3.2.4) | Yes, 0.5% and 0.1% round trip; long and short |
| With the averaged CL exit, all eight patterns earn 0.2-2.8% per trade net | Lu, Chen & Hsu (2015), Tables 1, 3, 6 | 1 | Dow, CL-3, EMA10: morning star +1.81% net, 91% winning trades, 1% value-at-risk -0.95%; evening star +1.65%, 89% winning; positive in all three sub-periods, weaker in 2006-2012 (§3.2.2) | Same sample | Yes, but see the reconciliation problem below |
| Four patterns on 20 US stocks: three not profitable | Wang & Wang (2019), ICCDA conference proceedings (abstract) | 1 (conference) | One of four patterns "useful" for reversals | Not stated | Not stated |

**The reconciliation problem in Lu, Chen and Hsu.** The CL-3 and MYR-3 measures in this paper use the same trades and the same entry (next open). CL-3 averages the returns to the closes of days 1, 2 and 3; MYR-3 is the return to the day-3 close. For the morning star on Dow stocks (1,657 trades), CL-3 is +2.31% gross with 91% winning trades and a 1% worst case of -0.95%. MYR-3 is +0.47% gross with 51% winning trades and a 1% worst case of -8.51%. For both to hold, the day-1 and day-2 returns would have to average about +3.2% on most trades, then give almost all of it back on day 3. Two-day gains of 3% on most trades, with one in a hundred losing more than 1%, are not plausible for Dow stocks. The same gap appears for every pattern, long and short, and on the NASDAQ sample. My reading, not the authors': the CL-3 figures probably carry a definitional or computational error, and I do not count them as evidence until someone reproduces them. Two smaller problems point the same way. In the extracted tables the asterisk for Step-SPA significance is attached to negative means too. And their "$1,000,000 into $31,043,736" for the morning star compounds per-trade means across overlapping trades. The MYR (fixed-exit) tables are internally consistent and agree with MYR 2006 in sign for most patterns, so this report uses them.

**Survivorship and the short side.** Both Lu, Chen and Hsu samples keep only stocks that existed for all of 1992-2012. That flatters longs and hurts shorts, because the names that collapsed or were dropped are gone. The short-side gross figures above are therefore, if anything, understated, and the long-side ones overstated.

**US, learned OHLC features (machine learning)**

| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| A neural network reading 5-, 20- and 60-day OHLC-plus-volume chart images predicts the cross-section of US returns, beyond momentum and reversal | Jiang, Kelly & Xiu (2023), *Journal of Finance* 78, 3193-3249 (SSRN version, full text) | 1 | CRSP, 1993-2019; trained on the first eight years, tested on "the remaining nineteen years". Decile long-short, 20-day hold, 5-day images: Sharpe 2.35 equal-weighted, 0.45 value-weighted (Tables 3-4); weekly rebalancing: Sharpe 4.9-7.2 equal-weighted, 1.4-1.7 value-weighted (Table 6 text); the long decile alone about 1.0 (EW). Turnover about 175% a month equal-weighted, "nearly identical" to short-term reversal's. The forecasts correlate most with dollar volume, size, reversal and illiquidity, and known signals explain 12% or less of them | Out of sample 2001-2019 | **No.** The authors say the weekly result is not meant as "achievable in practice". The short decile earns about 0 to -2% a year EW |

These are OHLC bars, not named candlestick patterns (the authors chose OHLC bars over candles to save pixels, §2.1). The result shows that the shape of recent daily bars carries return information. It does not show that any named pattern does, and most of the edge sits in small, illiquid names, the ones that cost the most to trade.

**Outside the US, or intraday (context, not decisive)**

| Source | Market | Finding | Costs |
|---|---|---|---|
| Marshall, Young & Cahan (2008), *Rev. Quant. Finance & Accounting* 31(2), 191-207 (paper not read; seminar abstract and Jönsson 2016 §1.4) | 100 largest Tokyo stocks, 1975-2004 (Jönsson says 1975-2002) | No value in the full period, three decades, or bull and bear markets; "not even consistently profitable before transaction costs" | n/a |
| Duvinage, Mazza & Petitjean (2013), *Quantitative Finance* 13(7), 1059-1070 (abstract; working-paper details from CXO Advisory) | DJIA 30, 5-minute bars, 1 Apr 2010 to 13 Apr 2011 | 83 rules: about a third beat buy-and-hold gross at Bonferroni; at 0.05% one-way cost only 5 significant on net mean return; under SSPA none beats buy-and-hold net; none of 2,047 combined systems does | Yes, 5 bp one-way |
| Tharavanij et al. (2017), *SAGE Open* 7(4) (abstract) | Thai SET50, 2006-2016 | Most reversal patterns not different from zero; RSI, stochastic and MFI filters do not help | Not stated |
| Mukansi, UCT master's thesis (full text, undated copy) | 60 JSE stocks, 2010-2019, CL method | One pattern (three outside down) predictive; after fees no pattern beats passive benchmarks | Yes |
| Lu, Shiu & Liu (2012); Zhu, Atri & Yegen (2016); Deng et al. (2022) (abstracts) | Taiwan 2002-2008; China 1999-2008; SSE50 2000-2018 | Some bullish patterns profitable; in China, some patterns work in low-liquidity or small liquid stocks; SSE50 bearish patterns not predictive | Not stated in the abstracts |

### 2. Any form that survives?

- **Short side.** In the one study that reports both sides with a fixed exit, the short side is at least as strong as the long side (Dow 0.29% vs 0.15% gross per trade; NASDAQ-100 0.65% vs 0.46%), on samples whose survivorship works against it. The evening star is the strongest single signal in both samples. Caginalp and Laurent's short side was weaker (0.27% vs 0.9%). No study prices borrow.
- **Small and mid caps, volatile names.** Signals are stronger on the more volatile NASDAQ-100 survivors than on the Dow (Lu, Chen and Hsu §3.2.4). Jiang, Kelly and Xiu's equal-weighted results are about five times their value-weighted ones, so the information is concentrated in smaller, less liquid stocks. No study of named patterns on US small or mid caps, net of costs, was found. INSUFFICIENT.
- **As a filter, with volume, or with trend.** No US study tests candlesticks as a filter on another signal. Thai stocks show no gain from oscillator filters. Lu, Chen and Hsu find the trend definition does not change their result. Jiang, Kelly and Xiu's images include volume, but the network does not isolate a volume-conditioned pattern. INSUFFICIENT.
- **At short holds.** Every positive result is at 1-3 days. MYR 2006 found 2-, 5- and 10-day holds "very similar" and none significant. Lu, Chen and Hsu's stars hold up at 10 days only on NASDAQ (MS +1.03%, ES +1.14% gross, Table 7).
- **Intraday.** No net edge at 5-minute bars on large caps (Duvinage 2013, one year of data).

**Turnover and cost per trade (our cost model, not evidence).**

| Item | Long | Short |
|---|---|---|
| Commission-free trading cost, 15 bp per side (uncalibrated, ADR 0023) | 30 bp per round trip | 30 bp per round trip |
| Borrow for a 3-day hold, at ADR 0024's 0 / 30 / 300 bp-a-year grid | none | 0 / 0.4 / 3.6 bp |
| Borrow for an HTB name at 30% a year, 3 days | none | about 36 bp |
| Alpaca terms (LS-16, LS-18) | fractional allowed | ETB at $0 borrow; no fractional shorts; $2,000 margin minimum for live; paper shows no borrow fees and no dividends, so the backtest must charge them |
| Other short frictions | | Payments in lieu of dividends; Rule 201 (after a 10% intraday drop a stock can be shorted only above the best bid for the rest of that day and the next), which bites exactly after the long black days in bearish setups |
| Smaller names (outside about the top 1,000 by dollar volume) | 15 bp per side understates the half-spread (HO-14 "far more for small caps") | same, and more names are HTB |

A book of 3-day trades, fully invested, turns over about 84 times a year (252 / 3), about 25% a year of trading cost at 30 bp a round trip. That is why the per-trade gross must clear the cost by a margin.

**Rung-1 arithmetic** (gross per trade from the fixed-exit tables, against 30 bp; the 3x line is 90 bp, as in the [Q1 note](2026-10-19-q1-repurchasers-rung12.md)):

| Cut | Published gross | After about a 58% decline (QI-2) | Break-even (30 bp) | 3x line (90 bp) |
|---|---|---|---|---|
| All 8 patterns, Dow survivors | 0.23% | 0.10% | fail | fail |
| All 8 patterns, NASDAQ-100 survivors | 0.56% | 0.24% | fail | fail |
| Stars only, Dow (chosen after seeing the tables) | 0.56% | 0.23% | fail | fail |
| Stars only, NASDAQ-100 (chosen after seeing the tables) | 1.06% | 0.45% | pass | fail |
| Shorts only, NASDAQ-100, plus 3.6 bp borrow | 0.65% | 0.27% | fail | fail |

Without the decline, the NASDAQ-100 stars pass the 3x line (1.06% against 0.90%) and the Dow stars pass break-even only. Using the decline follows the backlog's rule that priors sit at or below the publication-decay haircut. Lu, Chen and Hsu published in 2015, and their own data already show weaker results in 2006-2012.

### 3. Data snooping

| Study | Patterns tried | Other choices | Correction |
|---|---|---|---|
| CL 1998 | 8 (4 long, 4 short) | 1 trend rule, 1 exit | None for multiple rules |
| MYR 2006 | 28 (14 single lines, 14 patterns), both directions | 3 holds | Per-rule bootstrap (500 series); nothing passed |
| Horton 2009 | 8 | not known | Distribution tests per pattern (from the theses) |
| Lu, Chen & Hsu 2015 | 8 | 3 trend definitions x 4 exits x 2 markets x 2 cost levels = 384 reported cells | Step-SPA (B = 10,000) within each table of 8 patterns, by my reading of §3.1 (UNVERIFIED in the appendix); significance marks look misplaced in the extracted tables |
| Duvinage et al. 2013 | 83 rules, plus 2,047 combined systems | intraday timing rules | Bonferroni, then SSPA; nothing survives costs |
| Jiang, Kelly & Xiu 2023 | none named: one network per image size and horizon, 3 x 3 = 9 main specifications plus variants | network design choices made on the training years | Clean train/test split by date; no multiple-testing adjustment across specifications |
| Bajgrowicz & Scaillet 2012 (general technical rules, not candlesticks) | thousands of rules, DJIA 1897-2011 | n/a | False discovery rate; in-sample gains are wiped out by low costs, and the best rules cannot be picked ahead of time |

No study found uses White's reality check by name or a deflated Sharpe. The largest uncorrected fork is the exit rule: the strong results appear under the CL exit and mostly vanish under a fixed exit. Picking the stars, as §4's second trial does, is another fork: it was made after reading the tables, so its prior must carry that look.

### 4. The cleanest test, if run at all

This would be two trials in one new family, read as a rung-2 store run (no published long or short leg exists for candlesticks; Chen-Zimmermann has no candlestick signal as far as I know, UNVERIFIED). The design separates "the candle shape" from "the stock fell recently", which is the question that decides whether this is anything more than short-term reversal (SH-1, SH-2).

- **Signals.** CL's eight three-day reversal patterns, definitions frozen from Caginalp and Laurent (1998) §2-3, including its trend rule (3-day average falling, or rising, on 6 consecutive days, at most one violation). Bullish patterns open a long and bearish patterns open a short, at the next open (ADR 0023 D3).
- **Exit.** Fixed: the close of the third session after entry (MYR-3). No averaged exit, because of the reconciliation problem.
- **Control.** For each signal, a matched set of same-day stocks with the same 6-day return and volatility decile but no pattern, same entry and exit; the test statistic is pattern minus control. This is the clean way to ask whether the candle adds anything to reversal.
- **Universe.** The top 3,000 by median dollar volume with the default `universe.min_price` and `universe.min_median_dollar_volume` floors (ADR 0024 point 2), reported in two tiers: ranks 1-1,000 and 1,001-3,000. The state of corporate-actions coverage in that universe and the absence of an S&P answer key are stated at admission, as ADR 0024 requires.
- **Costs.** 15 bp per side in tier 1; 30 bp per side in tier 2 as the declared headline (an assumption standing in for the missing half-spread measurement; 15 bp reported alongside). Shorts: borrow at ADR 0024's 0 / 30 / 300 bp-a-year grid with 300 the headline, plus dividends owed. Names flagged HTB in the ADR 0021 record are excluded from the short leg after 2026-10-11, and before that date the 300 bp line stands in. Rule 201 days block the short entry.
- **Book.** Equal weight across open trades, gross exposure at most 1.0 (ADR 0024's placeholder), cash otherwise; daily cadence.
- **Trial budget: 2, both declared now.** (1) All eight patterns, long and short. (2) Morning and evening star only, disclosed as chosen after reading Lu, Chen and Hsu's tables (ledger L-1, rule L3).
- **Kill rule** (per trial, written before any read). On the rung-2 window ending 2019-12 (ledger rule L4; the store's bars start in 2016, so about four years), kill if any of these holds: (a) the net mean per trade, after the headline costs, is at or below 0 or its t is below 2; (b) the pattern-minus-control mean is below 10 bp per trade or its t is below 1.5; (c) the short leg alone is negative net at the 300 bp borrow line. If (c) fails and the long leg passes, the long leg goes forward as a long-only variant, which counts as a new trial.
- **Engine work** (only if it survives rung 2): a signal reading OHLC, per-position holds, a signed book with short-side costs (ADR 0024 requirements 1 to 3). Size M to L.
- **Information value.** A pass that survives the control would be the first post-2012 US evidence that named candles add to reversal. A fail with the control equal to the pattern retires named candlesticks as repackaged reversal, which is the more likely outcome.

**An intraday version (pointer only).** It would need intraday bars, which the store does not hold (budget: ADR 0009's $0/month or an owner decision), intraday `known_at` rules, a sub-daily engine, an intraday cost model and an execution build (ADR 0024, "Before any intraday paper book"). The only intraday evidence found (Duvinage 2013) is negative after 5 bp costs.

**Ladder ledger:** row L-1 (rung 1 on published figures only) and a batch row, added with this report and updated in revision 2.

**Parked-list row (proposed):** "Daily candlestick patterns, named, long or short | Rung 1 on published figures fails a 3x-cost line in every cut after the post-publication decline; the fixed-exit tables show a gross edge only for morning and evening stars (0.5-1.1% per trade, 1992-2012 survivors); the large averaged-exit figures are unexecutable (CL 1998) or cannot be reconciled (Lu-Chen-Hsu 2015); no post-2012 US net test. Revisit with a post-2012 US net-of-cost study or a funded rung-2 run of §4 | CS-1 to CS-7".

## Disconfirmation

- **Searches run:** the named studies (CL 1998, MYR 2006, Lu-Chen-Hsu 2015 and 2016, Horton 2009, Marshall-Young-Cahan 2008, Duvinage 2013); US post-2013 named-pattern studies (three phrasings, two extended); US small caps and Russell 2000 (extended); candlestick or OHLC features in machine learning (extended); Jiang-Kelly-Xiu and its replications (three); Quantpedia (search and site search); general technical-rule data snooping (Bajgrowicz-Scaillet).
- **Against a positive edge:** MYR 2006; Horton 2009; MYC 2008; Duvinage 2013; Tharavanij 2017; Mukansi (after fees); Bajgrowicz-Scaillet 2012; Lu-Chen-Hsu's own fixed-exit tables for six of eight patterns; their weaker 2006-2012 sub-period; and the reconciliation problem in their CL tables.
- **Against a null:** Lu-Chen-Hsu's fixed-exit stars, long and short, on two survivor samples; CL 1998's gross bounce; Jiang-Kelly-Xiu's out-of-sample OHLC predictability.
- **Not found:** any post-2012 named-pattern study on US stocks; any survivorship-free US test; any study pricing borrow for candlestick shorts; any published replication of Jiang-Kelly-Xiu net of costs (three GitHub replications exist, Tier 3, no net results).

## Caveats & gaps

- All named-pattern US evidence is 1992-2012, on 19 to about 500 large stocks.
- The gross figures in §1 and §2 are derived as published net plus the authors' 0.5% cost; trade-weighted pooling treats overlapping trades as independent.
- The reconciliation problem is my inference from the published tables; the authors do not discuss it.
- Our 15 bp per side is uncalibrated (ADR 0023), and the half-spread for names below the top 1,000 is unmeasured.
- Index-level candlestick studies (Meng et al. 2021 on S&P 500 index shadows; Jamaloodeen et al. 2018 on the S&P 500 index) were dropped: index timing is not a stock signal.

## UNVERIFIED items

- **Horton (2009):** not read. ScienceDirect returned 403, and RePEc lists the full text as restricted. Its design and conclusion (eight patterns, 349 S&P 500 stocks, distribution tests, no better than random) come from two theses (Mukansi §2.4.1, which cites it as both 2007 and 2009; Jönsson 2016 §1.4).
- **Marshall, Young & Cahan (2008):** not read; Springer redirects to a login. Findings are from a 2006 seminar abstract and Jönsson (2016); the end year differs between them (2004 vs 2002).
- **Duvinage, Mazza & Petitjean (2013):** the published paper was not read. Taylor & Francis is paywalled, RePEc lists no download, and the Louvain repository fetch failed. The rule count (83), cost (0.05% one-way), window (April 2010 to April 2011) and per-step counts come from CXO Advisory's summary of the August 2012 working paper (Tier 2), consistent with the journal abstract.
- **Lu, Chen & Hsu (2015):** read in the July 2015 working paper; the published version may differ. Whether Step-SPA spans each table of 8 or all cells is my reading.
- **Chen-Zimmermann** has no candlestick-based signal, as far as I know (not checked against SignalDoc.csv in this session).
- **Mukansi thesis:** the copy read is undated; its sample runs to 2019.

## Follow-up questions (not answered here)

- **Learned OHLC features (Jiang, Kelly and Xiu):** a separate brief on whether a value-weighted or liquidity-floored version survives 15-30 bp per side and borrow, post-2019, and how it overlaps weekly reversal. It falls under the ML validation brief's stage gates.
- **Execution, not signal:** whether a candle shape can time the entry of an existing book's trades (Detollenaere and Mazza 2014 found lower market-impact cost after a doji, in European stocks).

## Source log (kept or dropped, and why)

| Source | Read as | Kept / dropped | Why |
|---|---|---|---|
| Caginalp & Laurent 1998 | full text | kept | named positive US study |
| Marshall, Young & Rose 2006 | companion preprint, full text | kept | named negative US study |
| Lu, Chen & Hsu 2015 | working paper, full text with tables | kept | main positive US study; both sides |
| Lu, Chen & Hsu 2016 (*Applied Economics*) | abstract | kept as context | one-day patterns, DJIA 1974-2009; costs not in abstract |
| Horton 2009 | via two theses | kept, UNVERIFIED | US negative; primary unreachable |
| Jiang, Kelly & Xiu 2023 | SSRN version, full text | kept | only modern US OHLC study; ML |
| Wang & Wang 2019 | abstract | kept, low weight | small US sample, conference paper |
| Marshall, Young & Cahan 2008 | seminar abstract and thesis | kept, UNVERIFIED | Japan negative |
| Duvinage et al. 2013 | abstract and CXO summary | kept | intraday US negative |
| Tharavanij et al. 2017; Mukansi (full text); Lu-Shiu-Liu 2012; Zhu-Atri-Yegen 2016; Deng et al. 2022 | abstracts (Mukansi full) | kept as context | non-US |
| Bajgrowicz & Scaillet 2012 | abstract | kept | data-snooping benchmark for technical rules |
| Detollenaere & Mazza 2014 | abstract | kept for follow-up only | execution, Europe |
| Jönsson 2016 (Lund bachelor's thesis) | full text | used only as a secondary source for Horton and MYC | Swedish market, student thesis |
| Marshall 2005 (Massey PhD thesis) | front matter | dropped | same results as MYR 2006 |
| Röthig, Röthig & Chiarella 2015 (UTS QFRC 362) | full text | dropped | currency futures |
| Meng et al. 2021; Jamaloodeen et al. 2018 | abstracts | dropped | index timing |
| PLOS ONE 2021 pattern-recognition ML; Mann (UCL) OHLC thesis; Stanford CS231n vision-transformer report; VLM blog | abstracts | dropped | non-US, intraday mid-price, 10 tickers, or Tier 3 |
| Poudel (SSRN, small caps) | abstract | dropped | technical patterns generally, not candlesticks |
| QuantifiedStrategies, Bulkowski, Medium, TradingView, LuxAlgo, Investing.com | snippets | dropped | Tier 3 vendor or blog backtests |
| Quantpedia | site search | dropped | no candlestick strategy listing returned |
| Jiang-Kelly-Xiu GitHub replications (three) | snippets | dropped | Tier 3, no net-of-cost results |

## Sources

- Caginalp, G. & Laurent, H. (1998). The predictive power of price patterns. *Applied Mathematical Finance* 5, 181-205. doi:10.1080/135048698334637.
- Marshall, B. R., Young, M. R. & Rose, L. C. (2006). Candlestick technical trading strategies: Can they create value for investors? *Journal of Banking & Finance* 30(8), 2303-2323. https://ideas.repec.org/a/eee/jbfina/v30y2006i8p2303-2323.html
- Lu, T.-H., Chen, Y.-C. & Hsu, Y.-C. (2015). Trend definition or holding strategy: What determines the profitability of candlestick charting? *Journal of Banking & Finance* 61, 172-183. doi:10.1016/j.jbankfin.2015.09.009. Working paper: https://www.econ.sinica.edu.tw/~econ/pdfPaper/14-A010.pdf
- Lu, T.-H., Chen, Y.-C. & Hsu, Y.-C. (2016). Can 1-day candlestick patterns be profitable on the 30 component stocks of the DJIA? *Applied Economics* 48(35), 3345-3354.
- Horton, M. J. (2009). Stars, crows, and doji: The use of candlesticks in stock selection. *Quarterly Review of Economics and Finance* 49(2), 283-294.
- Jiang, J., Kelly, B. & Xiu, D. (2023). (Re-)Imag(in)ing price trends. *Journal of Finance* 78(6), 3193-3249. doi:10.1111/jofi.13268. SSRN 3756587.
- Marshall, B. R., Young, M. R. & Cahan, R. (2008). Are candlestick technical trading strategies profitable in the Japanese equity market? *Review of Quantitative Finance and Accounting* 31(2), 191-207.
- Duvinage, M., Mazza, P. & Petitjean, M. (2013). The intra-day performance of market timing strategies and trading systems based on Japanese candlesticks. *Quantitative Finance* 13(7), 1059-1070. Summary: https://cxoadvisory.com/technical-trading/testing-japanese-candlesticks-intraday-on-liquid-stocks
- Tharavanij, P., Siraprapasiri, V. & Rajchamaha, K. (2017). Profitability of candlestick charting patterns in the Stock Exchange of Thailand. *SAGE Open* 7(4). doi:10.1177/2158244017736799
- Mukansi, T. A study into the effectiveness of Japanese candlestick charting on the Johannesburg Stock Exchange from 2010 to 2019. Master's thesis, University of Cape Town. https://open.uct.ac.za/handle/11427/35958
- Jönsson, M. (2016). The predictive power of candlestick patterns. Bachelor's thesis, Lund University.
- Wang, M. & Wang, Y. (2019). Evaluating the effectiveness of candlestick analysis in forecasting U.S. stock market. *Proc. ICCDA 2019*. doi:10.1145/3314545.3314555
- Lu, T.-H., Shiu, Y.-M. & Liu, T.-C. (2012). *Review of Financial Economics* 21(2), 63-68. Zhu, M., Atri, S. & Yegen, E. (2016). *Pacific-Basin Finance Journal* 37, 116-127. Deng, S. et al. (2022). *SAGE Open* 12(3).
- Bajgrowicz, P. & Scaillet, O. (2012). Technical trading revisited: False discoveries, persistence tests, and transaction costs. *Journal of Financial Economics* 106(3), 473-491.
- Detollenaere, B. & Mazza, P. (2014). Do Japanese candlesticks help solve the trader's dilemma? *Journal of Banking & Finance* 48, 386-395.

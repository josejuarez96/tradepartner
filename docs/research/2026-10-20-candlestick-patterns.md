# Research Report: daily candlestick-pattern signals on US equities, net of costs

**Brief:** #1944  ·  **Date:** 2026-10-20  ·  **Status:** COMPLETE (within a size-S budget: 16 searches, 4 full texts read)  ·  **Agent/model:** researcher (team candlebrief), Claude Opus 5.5

**Decision this unblocks:** whether a daily candlestick hypothesis earns a backlog item and a signal build (a signal reading daily open, high, low and close, which no current signal does), or goes on the parked list.

**Terms used once.** A *candlestick* is one day's open, high, low and close drawn as a box (the *body*, open to close; white if the close is above the open, black if below) with lines (*shadows*) to the high and low. A *pattern* is a named shape of one to three such days: a *hammer* (small body, long lower shadow), a *doji* (open about equal to close), an *engulfing* day (today's body covers yesterday's), a *harami* (today's body sits inside yesterday's), a *morning star* or *evening star* (three days: a big move, a small pause, a big move back), *three white soldiers* (three rising white days). A *reversal* pattern is meant to mark the end of a short trend, so studies first define a trend (for example, "the 3-day average fell on most of the last 6 days"). *Data snooping* is finding a rule that worked by chance because many rules were tried; *White's reality check*, *SPA* and *Step-SPA* are tests that ask whether the best of many tried rules beats chance; the *deflated Sharpe* does the same for a Sharpe ratio, given the number of trials.

## Answer

**Verdict:** MIXED, leaning negative, for the build the issue asks about (a standalone daily candlestick signal on investable US stocks, net of our costs): conflicting peer-reviewed results, none after 2012, and costs at our scale take most of the published edge. By component: fixed-exit signals NOT SUPPORTED (CS-1); three-day reversals with an averaged exit MIXED (CS-2); filters, small caps and volume INSUFFICIENT (CS-4)  ·  **Confidence:** medium

The careful US tests split. Marshall, Young and Rose (2006) tested 28 single-day and multi-day signals on Dow stocks, 1992 to 2002, with a bootstrap, and found that none beats chance even before costs. The positive studies (Caginalp and Laurent 1998; Lu, Chen and Hsu 2015) find profit only for three-day reversal patterns after a short decline, measured with one particular exit (sell a third on each of the next three days). They use 1992 to 2012 data, survivor-only or pre-decimal samples, and one of them reports weaker results after 2006. The same Lu, Chen and Hsu data show the patterns are not profitable with a fixed exit day, which places the edge in the first one or two days after the signal. That makes it a dressed-up short-term reversal, which the register already grades NOT SUPPORTED in large US stocks (SH-1) and eaten by costs at high turnover (SH-2). I found no post-2012, survivorship-free, net-of-cost US study, and no study that uses a deflated Sharpe. At 15 bp per side, the published per-trade edges, after the usual post-publication haircut, sit below a 3x-cost safety line. The evidence points to the parked list (row below); the owner decides.

## The four questions

### 1. What the evidence says, after realistic costs

| Claim | Source | Tier | Key figure (quoted or read) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Three-day reversal patterns predict a 2-day bounce in S&P 500 stocks | Caginalp & Laurent (1998), *Applied Mathematical Finance* 5, 181-205 | 1 | Abstract: "statistical significance at the level of 36 standard deviations ... a profit of almost 1% during a two-day holding period"; p. 200: the four down-to-up patterns average 0.9%; about five buy signals a day across the S&P 500 | "Out-of-sample" only in the sense that the pattern definitions came from a 1992 practitioner book; data 1992-1996 | **Estimated, not modelled:** p. 201 says 0.56% to 0.76% per trade "after commissions and the bid-ask spread on a $100 000 trade", with the spread assumed at 0.1-0.3%. Buys at the close of the day the pattern completes (p. 200) |
| Candlestick signals have no value in large US stocks | Marshall, Young & Rose (2006), *Journal of Banking & Finance* 30, 2303-2323; read in the authors' companion preprint "Market Timing with Candlestick Technical Analysis" | 1 | Preprint, conclusion: "Neither bullish or bearish candlestick single lines or patterns provide market timing signals that are any better than what would be expected by chance"; 14 single lines and 14 reversal patterns; 2-, 5- and 10-day holds "very similar"; 500 bootstrap series; Opening White Marubozu (bullish) predicts a significantly negative return | Yes: DJIA stocks 1992-2002, a market other than the one the rules came from | Not needed: no gross edge to cost |
| Eight three-day reversal patterns are profitable after 0.5% round-trip costs and Step-SPA, with the Caginalp-Laurent exit only | Lu, Chen & Hsu (2015), *Journal of Banking & Finance* 61, 172-183; read in the working paper IEAS 14-A010 (July 2015) | 1 | §3.1: three white soldiers earns 0.23%, 0.45% and 0.19% per trade net (one per trend definition), 3-day CL exit; with the fixed-day exits (MYR-3, MYR-10) the patterns are "unable to produce positive results"; §3.2.2: "performance seems to be poor during the period 2006-2012" | Post-publication of CL (1998) but 26 surviving DJIA stocks only ("Stocks that failed to exist for the whole period are excluded", §3.1) | Yes, 0.5% round trip (and 0.1%); long and short legs; entry at the next open |
| One-day patterns may be profitable on DJIA stocks, more so after 1992 | Lu, Chen & Hsu (2016), *Applied Economics* 48(35), 3345-3354 | 1 | Abstract (RePEc): "several" one-day patterns "may be profitable"; predictive power rises "from 1992 onwards"; sample 1974-2009 | Abstract only | Not stated in the abstract (UNVERIFIED) |
| Candlestick patterns are not profitable in Japan | Marshall, Young & Cahan (2008), *Review of Quantitative Finance and Accounting* 31(2), 191-207 | 1 | Seminar abstract (not the paper): no value over 1975-2004, in three 10-year sub-periods, or in bull or bear markets | Yes | n/a (no gross edge); UNVERIFIED against the published text |
| Intraday candlestick rules do not beat buy-and-hold after costs and data-snooping control | Duvinage, Mazza & Petitjean (2013), *Quantitative Finance* 13(7), 1059-1070 | 1 | Abstract: about a third of rules beat buy-and-hold before costs at Bonferroni; "no single candlestick rule beats the buy-and-hold strategy after transaction costs" under the SSPA test | 5-minute bars, DJIA 30; sample 2010-2011 per a secondary summary (UNVERIFIED) | Yes |
| Most reversal patterns have mean returns not different from zero; RSI, stochastic and MFI filters do not help | Tharavanij, Siraprapasiri & Rajchamaha (2017), *SAGE Open* 7(4) | 1 | Abstract: SET50 stocks, July 2006 to June 2016, holds of 1, 3, 5, 10 days, both CL and MYR exits | Thailand, not US | Not stated in the abstract |
| Some patterns profitable in Taiwan and China | Lu, Shiu & Liu (2012) *Rev. Financial Economics*; Zhu, Atri & Yegen (2016) *Pacific-Basin Finance J.*; Deng et al. (2022) *SAGE Open* | 1 | Taiwan 50, 2002-2008; China 1999-2008 (low-liquidity and small liquid stocks); SSE50 2000-2018: "several bullish patterns" positive | Mixed; not US | Not stated in the abstracts |

**Reading.** The only peer-reviewed positive US results come from two groups and one family of patterns: three-day reversals after a short decline, scored by an averaged exit. The only rigorous US negative result (MYR 2006) uses a fixed 10-day exit, and Lu, Chen and Hsu reproduce that null with the same exit on their own data. So the literature does not truly disagree: averaged over days 1 to 3 after the signal there is a bounce; held to a single later day there is nothing. The bounce sits where short-term reversal and the bid-ask bounce live. The bid-ask bounce is the drift of a closing price that last printed at the bid back toward the middle of the spread; it shows in close-to-close data and cannot be traded. CL (1998) buy at the same close that completes the pattern, which no real order can do. Lu, Chen and Hsu enter at the next open, which removes that problem, but trade only 26 survivors.

### 2. Any form that survives?

- **As a filter on another signal.** No US study tests candlesticks as a filter on a momentum or value book. Marshall, Young and Rose say only that they "cannot rule out" a complementary use. The reverse (filtering candlesticks with RSI, stochastic or MFI) did not help in Thailand (Tharavanij 2017). INSUFFICIENT.
- **Small caps and volatile stocks.** Lu, Chen and Hsu report larger net returns on 19 surviving NASDAQ-100 stocks (morning star, CL-3: 3.18% against 1.81% on DJIA, §3.2.4); Zhu et al. (2016) find some patterns work in small liquid Chinese stocks. No US small-cap net-of-cost evidence was found. Small caps also cost far more to trade than our 15 bp (HO-14). INSUFFICIENT.
- **With volume or trend.** Every reversal test already conditions on a prior trend; Lu, Chen and Hsu find "the definition of trend does not appear to affect the results". No US study of a volume condition was found.
- **At short holds.** Short holds are where the positive results are (CL exit, days 1 to 3), and where costs bite hardest.

**Turnover and cost at 15 bp per side.** A 1-to-3-day hold turns the whole position over every trade: 30 bp a round trip. A book that stays invested in such trades makes roughly 252 / 2 ≈ 125 round trips a year, about 37% a year of cost drag if fully invested (arithmetic, not a measurement). Rung-1 arithmetic of [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md): the gross edge per trade must exceed 30 bp to break even, and about 90 bp to clear the 3x-cost safety line the [Q1 note](2026-10-19-q1-repurchasers-rung12.md) used. The published gross per trade is about 0.7-1.0% (CL's 0.9%; Lu-Chen-Hsu's net figures plus their 0.5% cost). After the post-publication haircut of about 58% (QI-2) that is about 30-40 bp: at break-even, well under the 3x line. This is desk arithmetic on published figures, not a store read.

### 3. Data snooping

| Study | Patterns | Other choices | Correction |
|---|---|---|---|
| CL 1998 | 8 three-day reversals | 1 trend rule, 1 exit | None for multiple rules; robustness of the two parameters "checked" |
| MYR 2006 | 28 (14 single lines, 14 patterns) | 3 holds (2, 5, 10 days) | Bootstrap per rule (500 series); no family-wise correction, but nothing passed anyway |
| Lu, Chen & Hsu 2015 | 8 | 3 trend definitions x 4 holding strategies = 96 pattern-rule combinations | Step-SPA (Hsu, Hsu & Kuan 2010), B = 10,000. From the text, the test is run within each table of 8 patterns (one trend, one exit), not across all 96 (my reading of §3.1, UNVERIFIED in the appendix) |
| Duvinage et al. 2013 | 83 patterns (secondary summary, UNVERIFIED) | market-timing rules and combined systems | SSPA; nothing survives costs |
| Bajgrowicz & Scaillet 2012 (general technical rules, not candlesticks) | thousands of rules on the DJIA 1897-2011 | n/a | False discovery rate; "even in-sample performance is completely offset by low transaction costs"; the best rules could not be picked ahead of time |

No candlestick study found uses White's reality check by that name or a deflated Sharpe. The largest unadjusted fork is the exit rule: the positive result appears under one exit and vanishes under the other, and the choice between them is made after seeing both.

### 4. The one cleanest test, if run at all

If the owner wants a test anyway, the cleanest single hypothesis the literature allows is:
- **Pattern set:** CL's four bullish three-day reversals (three white soldiers, three inside up, three outside up, morning star), definitions frozen from CL (1998) §2-3, including its trend rule (3-day average falling on 6 consecutive days, at most one violation). No bearish patterns (long-only).
- **Universe and fills:** the ADR 0006 top-1,000 universe, or `dollar_volume` for a forward book (ADR 0023 D4); enter at the next open (D3); exit a third at each of the next three closes (CL-3), as published.
- **Cadence:** daily signal; equal-weight across open trades, cash otherwise.
- **Trial budget:** 1, at most 2 (the second only for the MYR-3 exit, declared now as the falsifier: if MYR-3 is also positive the bounce is not an artefact of averaging).
- **Kill criterion:** mean gross per trade below 90 bp (3x a 30 bp round trip), or t below 2, on the rung-2 window ending 2019-12 (ledger rule L4: 2016-2019 in our store, so about four years).
- **Engine work:** a new signal reading OHLC, per-position holds and partial exits, which the engine does not have (ADR 0023 lists no per-position hold). Size: M.

Because the rung-1 arithmetic already sits at break-even, the information value is low: a pass would most likely be the short-term reversal the register already grades, and a fail retires nothing new.

**Ladder ledger:** row L-1 (rung 1 on published figures only) and a batch row, added with this report.

**Parked-list row (proposed):** "Daily candlestick patterns | Fixed-exit tests find no value in large US stocks (MYR 2006); the positive results (CL 1998, Lu-Chen-Hsu 2015) rest on a 1-3-day averaged exit, survivors or pre-2013 data, and look like short-term reversal (SH-1, SH-2); published gross per trade after the post-publication haircut is about at our 30 bp round trip, under 3x | CS-1 to CS-6".

## Disconfirmation

- **Searches run (16):** the three named studies; Lu-Chen-Hsu 2015 and 2016; Horton 2009; Duvinage-Mazza-Petitjean 2013; Detollenaere-Mazza 2014; Marshall-Young-Cahan 2008; Tharavanij 2017; Zhu-Atri-Yegen 2016; Deng et al. 2022; Lu-Shiu-Liu 2012; Bajgrowicz-Scaillet 2012; "candlestick patterns US stocks no predictive power after costs ... replication CRSP"; "candlestick patterns profitability US stocks 2019 2021 2023 net of transaction costs" (extended); "candlestick cross-section of stock returns CRSP Fama-MacBeth" (extended).
- **Against the positive claim (CS-2):** MYR 2006 (no value, 28 signals, bootstrap); MYC 2008 (Japan, none in 30 years); Duvinage 2013 (intraday, none after costs and SSPA); Tharavanij 2017 (Thailand, near zero, filters do not help); Bajgrowicz-Scaillet 2012 (technical rules in general offset by low costs); Lu-Chen-Hsu's own MYR tables and their weaker 2006-2012 sub-period. Searches for a post-2012 US replication, a CRSP-wide test or a cross-sectional (Fama-MacBeth) candlestick study found none.
- **Against the negative claim (CS-1):** Lu-Chen-Hsu argue the MYR null is an artefact of the fixed exit day; CL 1998 report a large gross bounce. Recorded as a conflict (CS-1 and CS-2 name each other).

## Caveats & gaps

- Every US result is pre-2013 and on 19 to 500 large stocks; none is on a survivorship-free broad universe, after decimalization with measured spreads, or after 2012.
- The two positive US studies include short legs (bearish patterns); our book is long-only. CL's long-side 0.9% is the relevant number; Lu-Chen-Hsu's tables mix both, and their per-pattern figures were read only for three white soldiers and morning star.
- The CL-vs-MYR gap is my reading of Lu-Chen-Hsu's §3.1 (CL-3 positive, MYR-3 not, same entry): that the edge sits in days 1-2. They do not state it in those words.
- Lu-Chen-Hsu's "$1,000,000 into $31,043,736" (§3.1, morning star, 1992-2012) compounds per-trade means on 26 survivors; I give it no weight.
- Practitioner backtests (Bulkowski, QuantifiedStrategies and similar) are Tier 3 and were not used.

## UNVERIFIED items

- Horton (2009), *Quarterly Review of Economics and Finance* 49(2), 283-294: a secondary source says it tests eight patterns on 349 S&P 500 stocks; its findings could not be read and are not used.
- Marshall-Young-Cahan 2008: findings from a 2006 seminar abstract, not the published paper.
- Duvinage 2013: the sample period (2010-2011), the 83-pattern count and the 0.05% cost come from a secondary summary (CXO Advisory), not the paper.
- Lu-Chen-Hsu 2016: costs, holds and the multiple-testing method are not in the abstract.
- The published Lu-Chen-Hsu (2015) may differ from the July 2015 working paper read here.
- Whether the Chen-Zimmermann open dataset has any candlestick-based signal (I know of none); if none, rung 2 needs our own store read.

## Follow-up questions (not answered here)

- Does a one-to-three-day bounce after a short decline survive in our own fills (Probe 3's open-fill slippage) at all? That is a short-term-reversal question, already parked (QI-17, SH-1).
- Could a candle shape (for example a long lower shadow on high volume) improve the entry price of an existing book's trades, as Detollenaere and Mazza (2014) found for market-impact cost in European stocks? An execution question, not a signal.

## Sources

- Caginalp, G. & Laurent, H. (1998). The predictive power of price patterns. *Applied Mathematical Finance* 5, 181-205. doi:10.1080/135048698334637. Full text read (copy at c.mql5.com/forextsd/forum/210/).
- Marshall, B. R., Young, M. R. & Rose, L. C. (2006). Candlestick technical trading strategies: Can they create value for investors? *Journal of Banking & Finance* 30(8), 2303-2323. https://ideas.repec.org/a/eee/jbfina/v30y2006i8p2303-2323.html. Companion preprint read in full (c.mql5.com/forextsd/forum/211/).
- Lu, T.-H., Chen, Y.-C. & Hsu, Y.-C. (2015). Trend definition or holding strategy: What determines the profitability of candlestick charting? *Journal of Banking & Finance* 61, 172-183. doi:10.1016/j.jbankfin.2015.09.009. Working paper read in full: https://www.econ.sinica.edu.tw/~econ/pdfPaper/14-A010.pdf
- Lu, T.-H., Chen, Y.-C. & Hsu, Y.-C. (2016). Can 1-day candlestick patterns be profitable on the 30 component stocks of the DJIA? *Applied Economics* 48(35), 3345-3354. https://ideas.repec.org/a/taf/applec/v48y2016i35p3345-3354.html (abstract)
- Marshall, B. R., Young, M. R. & Cahan, R. (2008). Are candlestick technical trading strategies profitable in the Japanese equity market? *Review of Quantitative Finance and Accounting* 31(2), 191-207. https://ideas.repec.org/a/kap/rqfnac/v31y2008i2p191-207.html
- Duvinage, M., Mazza, P. & Petitjean, M. (2013). The intra-day performance of market timing strategies and trading systems based on Japanese candlesticks. *Quantitative Finance* 13(7), 1059-1070. https://ideas.repec.org/a/taf/quantf/v13y2013i7p1059-1070.html (abstract)
- Tharavanij, P., Siraprapasiri, V. & Rajchamaha, K. (2017). Profitability of candlestick charting patterns in the Stock Exchange of Thailand. *SAGE Open* 7(4). doi:10.1177/2158244017736799 (abstract)
- Lu, T.-H., Shiu, Y.-M. & Liu, T.-C. (2012). Profitable candlestick trading strategies: The evidence from a new perspective. *Review of Financial Economics* 21(2), 63-68 (abstract)
- Zhu, M., Atri, S. & Yegen, E. (2016). Are candlestick trading strategies effective in certain stocks with distinct features? *Pacific-Basin Finance Journal* 37, 116-127 (abstract)
- Deng, S. et al. (2022). Can Japanese candlestick patterns be profitable on the component stocks of the SSE50 index? *SAGE Open* 12(3) (abstract)
- Detollenaere, B. & Mazza, P. (2014). Do Japanese candlesticks help solve the trader's dilemma? *Journal of Banking & Finance* 48, 386-395 (abstract)
- Bajgrowicz, P. & Scaillet, O. (2012). Technical trading revisited: False discoveries, persistence tests, and transaction costs. *Journal of Financial Economics* 106(3), 473-491 (abstract)
- Horton, M. J. (2009). Stars, crows, and doji: The use of candlesticks in stock selection. *Quarterly Review of Economics and Finance* 49(2), 283-294 (not read; UNVERIFIED)

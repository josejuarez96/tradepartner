# Research Report: G2 — Insider purchases after 2010, filing-date timed, non-microcap, net of costs

**Brief:** #228  ·  **Date:** 2026-09-26 (two passes)  ·  **Status:** COMPLETE, with one stated caveat. The brief's done-when is met: a §6.1 grade on three Tier 1 sources read in full, the returns table, the disconfirmation log and the recommendation. The literature found does not contain a post-2010, filing-timed, non-microcap test at *monthly* holding periods. The report states that as a finding (see Claim B) rather than leaving it as unread material.  ·  **Agent/model:** research agent (team declactic), claude-opus-5-5

This replaces the H2 entry in handoff §6.3. That entry was graded MIXED on unverified figures, with no disconfirmation search.

> Process note:
> - **Pass 1:** abstracts only. The agent had no shell for Firecrawl; PDFs could not be parsed, and SSRN, ScienceDirect, Wiley and MDPI returned 403.
> - **Pass 2:** the orchestrator converted the open items to markdown with Firecrawl. Now read in full:
>   - S1, the open-access article (CC BY 4.0), plus the author's dissertation chapter;
>   - S2, NBER w16454, plus the internet appendix;
>   - S5, the FoFI 2024 conference version;
>   - the MDPI JRFM 2025 paper.
>
>   S4's SSRN page gave the verified abstract only.
> - Pass 2 used no new searches and added no new sources. The MDPI paper holds no return figures, so it is not counted.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium. Medium-high that the short-horizon, filing-timed effect in liquid stocks is gone. Low on monthly-horizon magnitudes, because no post-2010 test exists in what was found.

The brief's question splits into two claims, graded separately in the style of the G4 report.

### Claim A: short holding periods (days)
*Buying liquid US stocks at or after the Form 4 acceptance time and holding for days earns a positive excess return since 2010.*

**NOT SUPPORTED.**
- **S1: Tier 1, peer-reviewed, full text.**
  - Design: 25,636 open-market purchase filings by directors and officers, Nov 2018 – Nov 2023. Timing is from EDGAR acceptance. Entry is the 30-minute VWAP after publication, or the next open if the filing came out after hours.
  - Most liquid quartile: abnormal return to the next day's close is **+0.03% (t = 0.37)**, gross of spreads and commissions (Table 3 Panel B).
  - Whole sample: 10-day return is **−0.05% (t = −0.54)** (Table 1).
  - The authors: "a trading strategy based on insider trade reports … is neither practical nor scalable … even without taking into account transaction costs" (§4).
- **S3: Tier 1 working paper, acceptance-timed, $30M–$500M.** A portfolio that skips day 1 earns no significant alpha, and the large-cap extension "fails every matched-comparison gate".
- **S4: SSRN abstract, now verified.** "70-80 percent of total alpha dissipating between the transaction date and the following trading day".
- **No post-2010, filing-timed source reports a positive figure in liquid stocks.**

### Claim B: monthly holding periods, the CMP and cluster style
*A monthly-rebalanced, filing-timed portfolio of opportunistic or clustered insider purchases in non-microcap stocks earns a positive excess return since 2010, net of costs.*

**MIXED, under the §6.1 criterion "only pre-2005 evidence".**
- **For (S2, full text).** Every supporting figure is from 1986–2007 and gross:
  - long-only value-weighted opportunistic buys: five-factor alpha **0.72%/month (t = 2.27)** (Table IV Panel B);
  - in the top half of stocks by market cap: **+0.55%/month (t = 2.31)** in a one-month-ahead return regression (Table IX);
  - with a return window that starts after the pre-SOX filing deadline: **1.00%/month (t = 5.26)** (Table A4).
- **Clusters (S9) and the Ali–Hirshleifer measure (S8)** are also pre-2015, gross, and timed on the trade date or with timing unverified.
- **The only post-2010 composite test (S5)** is timed on the trade date: "disregarding the reporting lag". Its authors say it is not real-time implementable. Its value-weighted alphas are "considerably or much weaker at 0.15% to 0.87%".
- **No post-2010, filing-timed, non-microcap, monthly-horizon test was found for or against.**

**Overall MIXED.** Claim A is NOT SUPPORTED. Claim B rests only on pre-2008, gross evidence, and the post-2010 evidence points against it:
- the short-horizon effect in liquid stocks is gone (S1);
- predictability is concentrated in small and equal-weighted portfolios (S1, S5);
- post-2005 anomaly returns outside microcaps are near zero (S10).

**Headline post-2010 figure.**
- **Liquid stocks (S1 top quartile by prior two-day dollar volume):** entry at the 30-minute VWAP after EDGAR acceptance, or the next open if published after hours, exit at the next day's close. Abnormal return **+0.03% (t = 0.37), gross**. Same-day exit: **+0.13% (t = 2.50)**.
- **Whole sample:** 5 days **+0.23% (t = 3.15)**; 10 days **−0.05% (t = −0.54)**.
- **No study reports a net figure.** S1 deducts no spread or commission. The agent infers that any realistic round-trip cost puts the liquid-stock figure at zero or below.

## Evidence
"Timing" is the event-time convention. Per the brief, figures timed on the trade date or trade month count as Tier 3 for this question, whatever the source's own tier.

| Claim | Source | Tier (source / for this question) | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Design of the post-2010, filing-timed test | S1 Oenschläger & Möllenhoff, *Finance Research Letters* 72 (Feb 2025) 106514, full text, §2 | 1 / 1 | "We used only filings of purchases … where the reporting person was Director or Officer and the trade has been disclosed no more than two trading days later"; transaction code "P"; "58,732 filings between November 2018 and November 2023", reduced to 25,636 (first filing of the day per company). "The exact time of publication provided by the SEC's acceptance date time was used". Buy at "the VWAP of the time window starting immediately after the time of publication and ending 30 min later"; filings published after hours or within 30 min of the close go to "the beginning of the next trading day". Abnormal return = stock minus FF5-beta × S&P 500 futures VWAP. Exit at the official close, "ignoring any market impact … bid–ask-spreads, or direct transaction costs". There is no market-cap filter; splits are by trading volume | Yes (2018–2023) | **No** (gross; also reports capacity-limited USD returns) |
| Whole-sample returns fade by day 10 | S1 Table 1 Panel A (N = 25,636) | 1 / 1 | Mean abnormal return, t0 / t1 / t5 / t10 days: **0.21% (t 7.61) / 0.33% (7.65) / 0.23% (3.15) / −0.05% (−0.54)**. Medians: 0.02% / 0.00% / −0.21% / −0.55%. In-hours filings only (N = 7,023): 0.14 / 0.46 / 0.31 / 0.05% (t10: t 0.30) | Yes | No |
| **Liquid stocks: nothing left by the next close** | S1 Table 3 (quartiles of dollar volume over the 2 days before publication) | 1 / 1 | t0: lower 25% **0.28% (t 4.36)**, middle **0.22% (5.83)**, upper 25% **0.13% (2.50)**. t1: lower **0.65% (7.30)**, middle **0.31% (5.29)**, upper **0.03% (0.37)**. 5-minute window (Table A.3), upper quartile t1: **0.16% (t 1.61)**. There is no t5 or t10 split | Yes | No |
| Returns fall with liquidity at every horizon | S1 Table 4 | 1 / 1 | Coefficient on log(TradeVol): t1 **−0.0008 (t −2.93)**, t5 **−0.0017 (−3.72)**, t10 **−0.0017 (−3.31)**. The overnight dummy is negative at t1 to t10 (t1 **−0.0029, t −2.40**) | Yes | No |
| Capacity-limited dollar returns are not significant | S1 Table 2; §3.1 | 1 / 1 | At 25% of volume in the buying window: mean USD t0 **2,565 (t 1.50)**, t1 **3,234 (1.30)**, t5 **−6,365 (−1.26)**, t10 **−12,071 (−2.30)**. Medians ~0. "a trading strategy would be no better than a coin flip" | Yes | No |
| Close-to-close CARs overstate what is achievable | S1 §3.1, Fig. 1 | 1 / 1 | CARs from end-of-day prices "are considerably higher, but not achievable, as they assume that a purchase is possible at the last closing price before the announcement" | n/a | n/a |
| Most filings arrive after the close | S1 fn. 9 | 1 / 1 | "The majority of the observed announcements (> 70% of total) are published after market close" | n/a | n/a |
| Filing-timed reaction is front-loaded (microcaps) | S3 Zhao, arXiv 2602.06198v2 (24 Sep 2026), abstract and HTML full text | 1 (WP, single author) / 1 | $30M–$500M; 13,534 purchase lines, 1,192 issuers, 2018–2024. Filings accepted before 16:00 ET are priced on the filing day, later ones on the next session. "The reaction runs for two to three sessions; the 29-day drift is imprecise (two-way t = 1.70) and a calendar-time portfolio that skips the first day earns no significant alpha." CAR[1,20] 3.2%, CAR[1,60] 4.6% (means, day 1 included). Calendar-time alpha with day 1: 2.10%/month (t 5.28) (§5.7, Table 12) | Yes | No |
| Large-cap extension fails | S3 abstract | 1 (WP) / 1 | "A separate large-cap extension schedules USD 29,075,559 a year of buyer-cluster flow but fails every matched-comparison gate." Design: **UNVERIFIED** | Yes | n/a |
| Most alpha arrives before disclosure | S4 Ozlen & Batumoglu, SSRN 5966834 (written 25 Dec 2025, posted 20 Jan 2026, 13 pp.), SSRN abstract | 1 (WP; authors unaffiliated or "Independent") / 1 for direction | Russell 2000 constituents. "Once entry is delayed until the filing date … measured performance collapses, with 70-80 percent of total alpha dissipating between the transaction date and the following trading day, well before any public disclosure. By the time a Form 4 filing is released, the remaining signal is largely confirmatory rather than informative." Sample months (Jan–Nov 2025): **UNVERIFIED** (Tier 3 relay only) | Yes | Not stated |
| CMP classifier is ex ante | S2 Cohen, Malloy & Pomorski, *JF* 67(3) 2012; NBER w16454 full text, pp. 11–13 | 1 / 1 (method) | "We require an insider to make at least one trade in each of the three preceding years … we define a routine trader as an insider who placed a trade in the same calendar month for at least three consecutive years … opportunistic traders as everyone else … We thus designate all insiders … at the beginning of each calendar year, based on their past history of trades". The history years are "not use[d] in our subsequent tests" (fn. 11). Look-back windows of 1–5 years give "similar results" (fn. 9) | n/a | n/a |
| CMP coverage and size tilt | S2 pp. 12–14 | 1 / 1 (method) | Final sample "about one-third the size of the entire sample of insider transactions", "tilted towards bigger stocks", "fewer micro-cap stocks … roughly twice the percentage of largest decile stocks". "roughly 64% of insider purchases … routine" | n/a | n/a |
| CMP long-only opportunistic buys | S2 Table IV (1986–2007; monthly, month after the trade month) | 1 / **3** (trade-month timing) | Opportunistic buys, **value-weighted**: CAPM **0.87 (t 2.88)**, FF3 0.64 (2.16), Carhart **0.52 (1.73)**, DGTW 0.57 (2.35), five-factor **0.72 (2.27)** %/month. Routine buys VW five-factor 0.09 (0.34). Equal-weighted opportunistic buys five-factor **1.58 (7.03)**. Long-short VW 82 bp (t 2.15), EW 180 bp (t 6.07) | No (1986–2007) | No |
| CMP portfolio timing | S2 p. 17 and fn. 15 | 1 | Portfolios are formed on the month's trades and held "over the month following these insider trades". The deadline then was "the tenth day of the following month"; the "median delay between trade date and report date … is 3 days" (fn. 7) | n/a | n/a |
| **CMP with filing-safe timing** | S2 Table A4 | 1 / 1 for timing; pre-2008 | Returns measured "from the 11th day of the month subsequent to when insiders trade … to the 10th day of the following month". Opportunistic buy coefficient: **1.43 (t 7.86)** raw, **1.05 (5.39)** with controls, **1.00 (5.26)** with month fixed effects; routine buy **0.20 (1.26)**. The authors: the results "are fully tradable in real-time" | No (1986–2007) | No |
| CMP size split | S2 Table IX | 1 / 3 (trade-month timing) | One-month-ahead return regression, opportunistic buy coefficient: **large stocks (top half by market cap) 0.55 (t 2.31)**; small stocks 0.80 (2.78). Routine buys: 0.08 (0.37) and −0.01 (−0.05) | No | No |
| CMP persistence | S2 p. 18 (Fig. 3) | 1 / 3 | The four-leg spread's 12-month event-time return is about 4% VW and 8% EW. "returns continue to rise for the first six months, and then level off" | No | No |
| CMP has no post-2002 subsample | S2 full text and internet appendix | 1 | No SOX or post-2002 split appears in either document | — | — |
| Post-2010 composite (includes clusters and CMP): timed on the trade date | S5 Heckmann, Jacobs & Schwarz, "Synthesizing Information-Driven Insider Trade Signals", FoFI 2024 version, full text, §3.1, §4.1 | 1 (WP) / **3** (trade-date timing) | "Our monthly rebalanced trading strategy is based on the actual trade date disregarding the reporting lag"; "we focus more on investigating the existence of insiders' short-term informational advantages rather than the actual real-time implementability". CMP routine trades are removed from the whole sample (trade-level approach, §2.1). Clustered months: at least 2 insiders net buying (after Alldredge & Blank). Sample 2000/2013 (varies by country) to 2021, 34 countries; the average US portfolio holds 1,378–1,666 firms | Partly | No |
| S5: value-weighted results much weaker; alpha is short-lived | S5 §1 | 1 (WP) / 3 | "The predictability is particularly driven by small firms, i.e., concentrated in equal-weighted portfolios, and by the long leg". Equal-weighted one-month alphas are "at least 1%". "In value-weighted portfolios, the corresponding results are considerably or much weaker at 0.15% to 0.87%". "at six-month … holding periods, the average monthly alpha in equal-weighted portfolios is roughly halved; the effect is even stronger for value-weighted portfolios". US-only country alphas (Tables 2–3): **UNVERIFIED** (not rendered in the conversion) | Partly | No; costs "likely particularly large for small firms" |
| Clustered purchases outperform (pre-2015) | S9 Alldredge & Blank, *Journal of Financial Research* 42(2): 331–360 (2019), abstract via OpenAlex | 1 / **3** (timing not stated) | "clustered insider purchases are followed by abnormal returns in excess of 2% during the subsequent month"; sample 1986–2014 (extract) | Mostly pre-2010 | No |
| Alternative opportunism measure (pre-2015) | S8 Ali & Hirshleifer, *JFE* 2017, author summary | 1 / **3** (timing not stated) | "a long-short trading strategy … generates a value-weighted abnormal return of 1.12% per month"; 1986–2014; insiders ranked on "past pre-QEA trades" at the start of each year | Mostly pre-2010 | No |
| Look-ahead in a related classifier | S5 fn. 5, citing S12 | 1 (WP) | The isolated-trade / trade-sequence measures of Biggerstaff et al. (2020) could not be used because the measure "requires to wait two more months after the trading month to make an appropriate classification" | n/a | n/a |
| Opportunistic insiders file after hours | S12 Biggerstaff, Cicero & Wintoki, *J. Corporate Finance* 64 (2020) 101654, search extract | 1 / **UNVERIFIED** | "when corporate insiders trade opportunistically, they report trades after hours" | n/a | n/a |
| Post-2005 non-micro anomaly returns near zero (general prior) | S10 Chen & Welch, arXiv 2607.06502 (Jul 2026), abstract | 1 (WP) / prior only | "Using only post-2005 and non-micro stocks reduces this to 7 bp. Even modest allowances for luck or transaction costs would have eliminated even these 7 bp." Insider signals are not named | Yes | Discussed |
| Form 4 deadline and deemed execution dates | S7 17 CFR 240.16a-3 (LII copy) | 1 (primary) | (g)(1): "Form 4 must be filed before the end of the second business day following the day on which the subject transaction has been executed". (g)(2)–(4): for 10b5-1 and discretionary trades the deemed execution date is the notification date, capped at the third business day. (k): website posting "by the end of the business day after filing" | n/a | n/a |
| Section 16 filings count as filed up to 10 p.m. | S6 17 CFR 232.13(a)(4) (LII copy) | 1 (primary) | Forms 3, 4 and 5 submitted "on or before 10 p.m. Eastern … shall be deemed filed on the same business day". The general cut-off is 5:30 p.m. ((a)(2)) | n/a | n/a |
| Electronic filing mandatory | S11 SEC Release 33-8230 | 1 (primary) | Effective and compliance date "June 30, 2003" | n/a | n/a |

## Reported returns by sample period, universe and timing convention
No source reports a return net of costs. All figures are gross unless marked. Rows are ordered from closest to the brief's specification to furthest.

| Source | Sample | Universe | Timing | Signal | Holding | Reported abnormal return | Gross / net | Gap to the brief |
|---|---|---|---|---|---|---|---|---|
| **S1**, top liquidity quartile | Nov 2018 – Nov 2023 | US; top 25% by prior 2-day $ volume | **EDGAR acceptance**; 30-min VWAP after, or next open | Director/officer open-market purchases (code P), filed within 2 trading days | Same day / 1 day | **+0.13% (t 2.50) / +0.03% (t 0.37)** | Gross | Net not reported; t5/t10 not split by quartile |
| S1, whole sample | same | US, all liquidity | same | same | 0 / 1 / 5 / 10 days | +0.21 / +0.33 / +0.23 / **−0.05%** (t 7.61 / 7.65 / 3.15 / −0.54) | Gross | Includes illiquid names |
| S1, whole sample, 5-min window | same | same | 5-min VWAP after acceptance | same | 0 / 1 / 5 / 10 days | +0.29 / +0.41 / +0.34 / +0.03% (t10: t 0.25) (Table A.1) | Gross | Same |
| S1, capacity-limited | same | same | 30-min VWAP, 25% of volume | same | 0 / 1 / 5 / 10 days | USD 2,565 / 3,234 / −6,365 / −12,071 (t 1.50 / 1.30 / −1.26 / −2.30) | Gross | — |
| S3 | 2018–2024 | $30M–$500M | **Acceptance**: pre-16:00 on the filing day, else next session | Code P purchases | 1–60 days; monthly calendar-time | CAR[1,20] 3.2%, CAR[1,60] 4.6%; 2.10%/month (t 5.28) with day 1; **skipping day 1: n.s.** | Gross | Microcap / small |
| S3, large-cap extension | UNVERIFIED | Large caps | Acceptance | Buyer clusters | UNVERIFIED | "fails every matched-comparison gate" | — | No figure |
| S4 | 2025 (months UNVERIFIED) | Russell 2000 | Trade date vs filing date | Form 4 trades | Not stated | 70–80% of alpha before the day after the trade | Not stated | Short sample; no figure |
| S5 | ~2000/2013 – 2021 | 34 countries (US portfolio ~1,378–1,666 firms) | **Trade date** (reporting lag ignored) | Composite: clusters, Ali–Hirshleifer opportunism, CFO, etc.; CMP routine trades removed | 1 month (6-month variant) | VW 0.15–0.87%/month (regional aggregates); EW ≥1%/month | Gross | Trade-timed (Tier 3 here); US values UNVERIFIED |
| S2, filing-safe timing (Table A4) | 1986–2007 | US (tilted to large caps) | **From the 11th of month t+1** (after the pre-SOX deadline) | Opportunistic buys | 1 month | Regression coefficient 1.00%/month (t 5.26), month fixed effects | Gross | Pre-2008; regression, not a portfolio |
| S2 (Table IV) | 1986–2007 | US | Month after the trade month | Opportunistic buys, long-only | 1 month | VW five-factor **0.72%/month (t 2.27)**; EW 1.58 (7.03) | Gross | Pre-2008; trade-month timing |
| S2 (Table IX) | 1986–2007 | US top half by market cap | Month after the trade month | Opportunistic buys | 1 month | Coefficient 0.55%/month (t 2.31) | Gross | Pre-2008; trade-month timing |
| S9 | 1986–2014 | US | UNVERIFIED | Clustered purchases | 1 month | "in excess of 2%" | Gross | Mostly pre-2010; timing |
| S8 | 1986–2014 | US | UNVERIFIED | Ali–Hirshleifer opportunistic insiders (long-short) | 1 month | VW 1.12%/month | Gross | Mostly pre-2010; long-short |
| S10 (prior) | 2006 onward | Top 3,000, top 90% of market cap | Monthly | ~200 anomalies (insider not named) | 1 month | Median 7 bp/month | Gross | Not insider-specific |

## Point-in-time notes for the Form 4 ingest scope
These are facts from the sources; the design choices are the owner's.
- **Use the EDGAR acceptance timestamp, not the filing date.** Under Reg S-T 232.13(a)(4), a Form 4 submitted up to 10 p.m. ET is deemed filed that business day. More than 70% of purchase filings in S1's sample were published after the close. A backtest that buys at the "filing date" close would therefore use information that was not yet public at that close, for most filings.
- **S1's "5:30 pm" statement conflicts with the rule.** S1 (§2) says "the SEC accepts live submissions only until 5:30 pm". The primary rule, S6 (a)(4), sets 10 p.m. for Forms 3, 4 and 5. The primary rule governs.
- **After-hours filings carry a different, lower return.** S1's after-hours ("overnight") dummy is negative from t1 to t10. S12 (extract only) says opportunistic insiders report after hours.
- **Close-based event studies overstate what can be captured.** S1 Fig. 1: end-of-day CARs "are considerably higher, but not achievable". A daily close-to-close system entering at the first close after acceptance enters later than S1's 30-minute VWAP, so it would capture no more than S1 reports, and probably less.
- **The filing lag can exceed two days.** For 10b5-1 and discretionary trades, the deemed execution date can be up to three business days after the trade (S7 (g)(2)–(4)).
- **A point-in-time CMP classifier is feasible, but costly in history.** It needs at least three prior calendar years of each insider's trades, assessed at the start of each year (S2 pp. 11–13). About two-thirds of transactions fall out of the classified sample (S2 p. 12). A 2010-start backtest would need Form 4 history from 2007 at the latest.
- **Regime dates:** the two-day rule has applied since 2002 (S2 fn. 7; the exact date, 29 Aug 2002, comes from a law-firm extract and is UNVERIFIED); electronic filing has been mandatory since 30 Jun 2003 (S11).

## Disconfirmation
**Pass 1:** 17 searches, of which 9 were aimed at disconfirmation. They are listed in the pass-1 log below.

**Pass 2:** no new searches. The disconfirmation came from reading the full texts: S1 Tables 3–4 and footnotes; S2 §II, Table IX and Table A4 plus the appendix; S5 §1–§4 and footnote 5; the MDPI paper.

**Against the claim, by brief item:**
1. **No post-2010 premium outside microcaps.**
   - S1: the most liquid quartile earns +0.03% (t 0.37) to the next close, gross. The liquidity coefficient is negative at every horizon.
   - S3: the large-cap extension fails every gate.
   - S5: value-weighted results are "considerably or much weaker".
   - S10: post-2005 non-micro anomalies earn a median of 7 bp.
   - **No post-2010, filing-timed source reports a positive non-microcap figure.**
2. **The premium is concentrated in the first days after filing.**
   - S1: whole-sample returns peak at t1 (+0.33%) and are gone by t10 (−0.05%). Fig. 2 shows "most of the return of the first and second trading days" is intraday after the announcement.
   - S3: the reaction lasts 2–3 sessions, and skipping day 1 leaves no significant alpha.
   - S4: 70–80% of the alpha comes before disclosure.
   - S5: alpha is short-lived; the value-weighted monthly alpha falls more than half at six months.
   - A daily close-to-close system cannot reach the intraday reaction.
3. **Look-ahead in the opportunistic classifier.**
   - **Not found for CMP.** The classifier is assigned at the start of each year from prior years only (S2 pp. 11–13), and the classification years are excluded from tests (fn. 11).
   - Two related points were found:
     - (a) CMP's main portfolios include trades from late in month t that, under the pre-SOX rule, may not have been public at formation. The authors address this with Table A4 (returns from the 11th of month t+1); the effect is unchanged.
     - (b) A related classifier (Biggerstaff et al., trade sequences) needs data from two months after the trade, so it is not point-in-time (S5 fn. 5).
   - **A replication risk:** the MDPI JRFM 2025 paper's abstract inverts CMP's definition ("Opportunistic insiders place a trade in the same calendar month for at least three consecutive years"). A point-in-time implementation should be checked against S2's text, not against secondary descriptions.

**For the claim:**
- S2: opportunistic buys predict returns in large stocks too (0.55%/month, t 2.31) and under filing-safe timing (Table A4). The effect builds over six months with no reversal. All of this is pre-2008 and gross.
- S1: short-horizon percentage returns are positive and significant in the whole sample up to t5, gross.
- S3: large gross returns in microcaps.

**MDPI JRFM 2025 ("Herding Insider Traders: The Case of Opportunistic Insiders", 2014–2024, FactSet).**
- It tests herding through return dispersion. It runs no return or alpha test and is timed on "the transaction date of the trade on SEC Form 4".
- It supplies no post-2010 CMP or cluster return figure, so it is not a figure source and is not counted.

## Recommendation on whether H2 enters the Phase 3 queue
The brief asks for this paragraph. It reads the evidence only; the owner decides.

- **Short-horizon version.** The evidence does not support registering H2 in the form the charter's system could trade: daily bars, long-only, liquid stocks, entry at or after the first close following acceptance. The best post-2010, filing-timed source (S1) finds essentially nothing in liquid stocks by the next close (+0.03%, t 0.37, gross), and nothing at 10 days in any stock. The effect that exists is intraday, in illiquid names, and not scalable.
- **Monthly version (CMP opportunistic or clustered buys).** This is the only version with a positive record in larger stocks: 0.5–0.7%/month, gross, 1986–2007. It has no post-2010, filing-timed test in the literature found.
- **If the owner wants H2 in the queue**, the evidence points to:
  - an exploratory slot after H1;
  - a pre-registered prior centred near 0 (not the 0.5–0.8%/month pre-2008 gross figure);
  - monthly rebalancing;
  - an acceptance-timestamp event clock;
  - a point-in-time CMP classifier needing Form 4 history from at least three years before the test start.
- **The Form 4 ingest scope** would then need acceptance timestamps, transaction codes, reporter roles and at least three years of history before the first test year. Whether that cost is justified for a hypothesis with a near-zero prior is the owner's call.

## Caveats & gaps
- **No post-2010 monthly-horizon test.** No source tests a monthly-rebalanced, filing-timed, non-microcap insider-purchase portfolio after 2010. Claim B's grade rests on pre-2008 support and indirect post-2010 evidence against it. This is a gap in the literature found, not unread material. It would take the system's own backtest to close (see Follow-up questions).
- **S1's design:**
  - Its "liquid" split is by dollar volume over the two days before filing, not by market cap or NYSE breakpoints.
  - Its horizon stops at 10 days.
  - It removes CMP routine trades in spirit only, through transaction code P: "Only opportunistic trades are used, i.e. direct security purchases and not grants, awards". **S1's "opportunistic" is not CMP's classifier.**
  - It has no cluster filter.
  - It measures abnormal return against FF5-beta × S&P 500 futures, not a factor alpha.
  - Its whole-sample means are driven by outliers; medians are about 0.
- **S3** is a single-author working paper, dated two days before this report. Its large-cap result is one line in the abstract.
- **S4** is a 13-page paper by unaffiliated authors. Its sample dates come only from a Tier 3 relay.
- **S2's figures are regressions and portfolios from 1986–2007**, in a pre-SOX filing regime with a 10-day deadline. Post-publication decay since 2012 is not measured for this signal specifically. The general figures (McLean & Pontiff; Chen & Velikov) are in the G1 and G4 reports.
- **S5's US-specific country alphas** (Tables 2–3) were not rendered in the conversion. They are trade-timed in any case.

## UNVERIFIED items
- S3: the large-cap extension's design and figures.
- S4: sample months (Jan–Nov 2025 per a Tier 3 relay), size splits, figures.
- S5: US-only raw returns and alphas (Tables 2–3); the per-country start year (Table 1); JFQA acceptance.
- S8: long leg alone; portfolio timing.
- S9: event timing (trade date vs filing date); the 23% same-day figure.
- S12: the after-hours reporting finding (search extract only).
- The exact start date of the two-day rule (29 Aug 2002; law-firm extract).
- The handoff's "5.2% six-month alpha" and "7.4% cluster" figures: still unverified, and none of the Tier 1 sources reports them.

## Follow-up questions (not answered here)
- What does a monthly-rebalanced, acceptance-timed portfolio of CMP-opportunistic (or clustered) purchases earn in non-microcap US stocks from 2010 to 2026, net of costs? This is a code computation after a Form 4 ingest, and it is the only way to close Claim B.
- Does S1's liquid-quartile result hold at 20–60 day horizons? S1 stops at 10 days.
- Insider sales, 13D and 13F signals: out of scope for this brief.

## Sources
Retrieved 2026-09-26. Pass-2 full texts were supplied by the orchestrator as Firecrawl markdown.
- **S1** Oenschläger, E. & Möllenhoff, S. (2025), "Insider filings as trading signals — Does it pay to be fast?", *Finance Research Letters* 72, 106514 (open access, CC BY 4.0). https://www.sciencedirect.com/science/article/pii/S1544612324015435 (Tier 1; **full text, pass 2**). Also the dissertation chapter, Oenschläger, "Three papers in Empirical Finance on Information Processing", https://d-nb.info/1352383527/34 (same study; read for consistency only). Abstract: https://ideas.repec.org/a/eee/finlet/v72y2025ics1544612324015435.html
- **S2** Cohen, L., Malloy, C. & Pomorski, L. (2012), "Decoding Inside Information", *JF* 67(3): 1009–1043. NBER w16454: https://www.nber.org/system/files/working_papers/w16454/w16454.pdf (Tier 1; **full text, pass 2**). Internet appendix: https://afajof.org/wp-content/uploads/files/supplements/Decoding_Inside_Information-.pdf. Page numbers are the NBER version's. Some table labels in the conversion are garbled by OCR ("DAPM", "Pama-French", "Farhart", "D-Factor"); they are read here as CAPM, Fama-French, Carhart and five-factor.
- **S3** Zhao, H. (2026), "Insider Purchases Far Below the 52-Week High: Decomposing the Disclosure Reaction in Microcap Equities", arXiv 2602.06198v2. https://arxiv.org/abs/2602.06198 ; https://arxiv.org/html/2602.06198 (Tier 1 WP; full text HTML).
- **S4** Ozlen, O. & Batumoglu, O. (2025), "The Death of Insider Trading Alpha: Most Returns Occur Before Public Disclosure", SSRN 5966834. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5966834 (Tier 1 WP; **abstract verified from the SSRN page in pass 2**).
- **S5** Heckmann, J., Jacobs, H. & Schwarz, P., "Synthesizing Information-Driven Insider Trade Signals", SSRN 4537187; FoFI 2024 version: http://wp.lancs.ac.uk/fofi2024/files/2024/04/FoFI-2024-009-Jens-Heckmann.pdf (Tier 1 WP; **full text, pass 2**).
- **S6** 17 CFR 232.13. https://www.law.cornell.edu/cfr/text/17/232.13 (Tier 1 primary; LII copy).
- **S7** 17 CFR 240.16a-3. https://www.law.cornell.edu/cfr/text/17/240.16a-3 (Tier 1 primary; LII copy).
- **S8** Ali, U. & Hirshleifer, D. (2017), *JFE*. Author summary: https://corpgov.law.harvard.edu/2015/09/22/opportunism-as-a-managerial-trait/ (Tier 1 via author summary).
- **S9** Alldredge, D. & Blank, B. (2019), *Journal of Financial Research* 42(2): 331–360. https://api.openalex.org/works/doi:10.1111/jfir.12172 (Tier 1; abstract).
- **S10** Chen, A. Y. & Welch, I. (2026), "What Useful Alphas?", arXiv 2607.06502. https://arxiv.org/abs/2607.06502 (Tier 1 WP; abstract).
- **S11** SEC Release 33-8230. https://www.sec.gov/rule-release/33-8230 (Tier 1 primary; landing page).
- **S12** Biggerstaff, L., Cicero, D. & Wintoki, M. B. (2020), *Journal of Corporate Finance* 64, 101654. https://www.sciencedirect.com/science/article/abs/pii/S0929119920300985 (Tier 1; search extract only).

**Read but not used as evidence (not counted):**
- The MDPI JRFM 2025 herding paper, https://www.mdpi.com/1911-8074/18/11/629: no return figures.
- The CRA literature watch, Q1 2025, pass 1: nothing relevant.
- paperswithbacktest.com's relay of S4 (Tier 3), pass 1.

## Budget log
- **Pass 1:**
  - 17 of 20 searches.
  - 12 distinct works cited. S5 and S12 came from search extracts only.
  - 13 if the CRA page is counted, one over the cap; recorded rather than hidden.
- **Pass 2:**
  - 0 new searches; 3 remain unused. A search for a post-2010 monthly-horizon test would have needed a 13th source, over the cap. That test is listed as a follow-up computation instead.
  - 0 new sources. Full texts of S1, S2 and S5 were read, and S4's abstract was verified. The MDPI paper was read and is not counted, because it holds no return figures.
- **Status change:** from INCOMPLETE to COMPLETE.
- **Verdict change:** none (MIXED). It is now split into Claim A (NOT SUPPORTED) and Claim B (MIXED, only pre-2008 evidence).
- **Confidence change:** from low to medium.

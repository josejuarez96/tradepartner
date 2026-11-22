# Research Report: Rules that trade a handful of liquid ETFs: does any beat buy-and-hold SPY?

**Brief:** #3671  ·  **Date:** 2026-11-20  ·  **Status:** INCOMPLETE for verification. The verdict and the ranking are complete. Two full-text hosts were unreachable from this session (lehigh.edu, alphaarchitect.com, so no more fetches were tried), and every figure from a source new to this report was read from search results, abstracts or secondary summaries, so it is marked UNVERIFIED. The figures carried over from [2026-09-25-trend-timing](2026-09-25-trend-timing.md) (TT-*) were verified from full text in that report.  ·  **Agent/model:** team etfedge

All sources were accessed on **2026-11-20**. The brief as relayed set no numeric budget. This pass used 30 web searches and 2 fetches, both of which failed (see the Budget note).

## Answer
**Verdict:** NOT SUPPORTED for the question as the owner frames it ("beats buy-and-hold SPY in return, net of costs and taxes, over a full cycle"). That holds for every candidate. Several rules are MIXED for the narrower claim "lower drawdown or a higher Sharpe at equal or lower return".  ·  **Confidence:** medium-high on the direction (the evidence and the arithmetic agree, and they agree with the repo's earlier verified trend-timing work). Medium-low on the individual magnitudes, which are UNVERIFIED in this pass.

In plain English:

1. **No ETF timing or rotation rule has credible, independent, post-publication evidence of beating buy-and-hold SPY in return after costs, let alone after taxes in a taxable account.** None of the nine candidates clears that bar. The strongest results in this literature are all of one kind: **similar or lower return, with lower volatility and shallower crashes.**
2. **One reason is structural, not empirical.** A long-only cash account that sometimes steps out of SPY (into T-bills, bonds or gold) can beat SPY only if the days or months it skips earn less than what it holds instead. For calendar rules (pre-FOMC, pre-holiday, Halloween, turn-of-month) and trend rules, the skipped periods have, on average, still earned more than T-bills since the 1990s. So these rules give up return by construction. Only leverage (leveraged ETFs, or vol-targeting above 1x) can lift the expected return above SPY's, and that adds market exposure rather than edge.
3. **The candidates that once had a real edge have decayed or failed live:**
   - **Pre-FOMC drift:** "essentially disappeared after 2015" (Kurov, Wolfe & Gilbert 2021).
   - **The overnight-return pattern:** the only ETFs built to harvest it (NightShares NSPY and NIWM) closed in August 2023. NSPY was down 6.9% from its June 2022 launch while the S&P 500 rose 22%.
   - **Pre-holiday returns** in the S&P 500 are not significant from 1983 to 2019.
   - **Tactical asset allocation (Faber GTAA)** trailed a US 60/40 by about 4.3 pp a year after the 2009 bottom (practitioner estimate, 2017).
   - **Volatility-managed portfolios** do not beat their unmanaged versions out of sample across 103 strategies (Cederburg et al., JFE 2020). The market portfolio is the one exception that survives costs in one study (Barroso & Detzel, JFE 2021).
4. **What is left is risk reduction:**
   - **Trend filters** on equities (already graded MIXED as TT-1).
   - **Multi-asset trend following**, which was flat to negative in 2009-2013 and returned +27% (SG Trend Index) in 2022, when SPY fell.
   - **Volatility targeting** of the market, which mostly trims the left tail.

   Measured against SPY's *return*, each of these is a loss. Measured against a 60/40 or volatility-matched mix (*risk-adjusted*), the best out-of-sample estimate for a US trend overlay is about **+0.3 pp a year** over a static SPY/T-bill mix at the same volatility. That is derived from Zakamulin's verified +7% Sharpe (TT-2); the in-sample figure is about +1.6 pp (Clare et al., TT-5).
5. **A forward paper exam cannot tell any of these from noise.** ADR 0016's group-sequential bounds (t ≥ 4.762 / 3.367 / 2.750 at 12 / 24 / 36 months) need a net information ratio of about **4.8, 2.4 or 1.6** against the benchmark. The best plausible IR for these rules against SPY is about 0, and about 0.04 to 0.2 against a volatility-matched mix. At IR 0.2, t = 2 takes about **100 years**. Only a long historical test can say anything, and it has largely been run already (Zakamulin 2014).
6. **Taxes make the return comparison worse, never better.** Buy-and-hold SPY defers its gains and pays qualified-dividend rates. Every rule here realizes gains more often, mostly short-term (HO-20). Whipsaw exits and re-entries within 30 days create wash sales (G7-1). The daily overnight trade turns all of SPY's return into short-term gains each year, and it likely makes its dividends non-qualified (UNVERIFIED; IRS holding-period rule not re-read here).

**Direct answers.**
- **(i)** No. No ETF-level rule found has credible evidence of beating buy-and-hold SPY in return over a full cycle, net of costs and taxes.
- **(ii)** Yes, some reduce drawdown at similar or lower return: the trend filter, multi-asset trend and market vol targeting. Under ADR 0005 and the owner's 2026-10-03 decision (#659), that is not the objective, so none of them is a candidate as things stand. If the owner chose to amend the objective to risk-adjusted return against a volatility-matched benchmark, the cleanest single registered test would be the one under "If the owner amends the objective" below:
  - one frozen rule (SPY 10-month SMA on the total-return series, month-end signal, next-session fill, otherwise T-bills);
  - one frozen benchmark (a static SPY/T-bill mix whose weight is fixed before the test from pre-test data);
  - judged on a long historical index series;
  - with its expected result already known: about +0 to +7% Sharpe. ADR 0005's reversibility clause also applies: an amendment must cite results that were not the reason for it.

## Per-candidate table
Ranked by strength of post-publication net evidence for *any* benefit. The "vs SPY return" column is the owner's objective.

| Rank | Candidate | Core claim and original sample | Post-publication / OOS, net | Grade: beats SPY in return | Grade: risk-adjusted benefit vs 60/40 or vol-matched mix | Independent bets / yr | Years to tell from noise (t = 2) |
|---|---|---|---|---|---|---|---|
| 1 | (a) Multi-asset time-series momentum / trend | TSMOM in 58 futures, Sharpe > 1 gross, 1985-2009 (MOP 2012, TT-9); a century of trend net of 2/20 (HOP 2017) | Weak after crises: SG CTA Trend Sub-Index **−0.8%/yr** Jan 2009-Jun 2013 against +8.0% over the prior five years (Hutchinson & O'Brien, UNVERIFIED). AQR: the 2010s were weak because there were "fewer large moves", not because the strategy broke (Babu et al. 2020, UNVERIFIED). SG Trend **+27.35%** in 2022 (UNVERIFIED). Asset by asset, OOS R² is negative for the S&P 500 (TT-6) | **NOT SUPPORTED** | **MIXED** (a diversifier for 60/40; the long-only ETF form loses most of it) | ~2-4 regime changes; crises are the real bets (a few per decade) | vs SPY: never (expected excess < 0). vs 60/40: decades |
| 2 | (c) Volatility targeting the market | Scale exposure by 1/past variance: market alpha **4.9%**, Sharpe +25% (Moreira & Muir 2017, UNVERIFIED) | No systematic OOS gain across 103 strategies (Cederburg et al. 2020). The market is the one portfolio whose managed version is "profitable after transaction costs" (Barroso & Detzel 2021). A look-ahead in the calibration; drawdowns of 68-93% in rolling-window versions (secondary, UNVERIFIED). Lower left tail for equities (Harvey et al. 2018) | **NOT SUPPORTED** without leverage (average exposure ≤ 1) | **MIXED** (market only; the effect is mainly risk control) | ~12 (monthly rescaling), effectively a few high-vol regimes | vs a vol-matched mix: decades to centuries |
| 3 | Trend filter on SPY (context: B2, TT-1; not re-researched) | 10-month SMA: Sharpe 0.54 vs 0.39 at equal return, 1952-2011 (TT-5) | OOS: Sharpe **+7%**, mean **−20%** vs BH (TT-2); gains come from four bear markets (TT-3) | **NOT SUPPORTED** (TT-10) | **MIXED** (TT-1) | ~1-2 switches; ~4 bear markets in 80 years | ~100 yr at IR 0.2 (in-sample); far more OOS |
| 4 | (b) Dual momentum / GTAA | Faber: 10-month SMA on 5+ asset classes, 1972-2005, "equity like returns with bond like volatility". Antonacci GEM | GTAA trailed US 60/40 by **4.3 pp/yr** after the 2009 bottom (Alpha Architect 2017, T2, UNVERIFIED). Faber's paper underperformed buy-and-hold in each year 2009-2012 (secondary, UNVERIFIED). GEM's 12-month spec "regressed to the median" OOS (Investresolve review, T3, UNVERIFIED) | **NOT SUPPORTED** | **MIXED** (drawdown reduction; no independent Tier 1 OOS) | ~12 rebalances; ~1-2 regime bets | vs SPY: never; vs 60/40: decades |
| 5 | (e) Turn-of-month | 1926-2005: "no reward for bearing market risk except at turns of the month" (McConnell & Xu 2008, UNVERIFIED) | Gone in S&P futures and spot after 1990 (Maberly & Waggoner 2000, UNVERIFIED). Sector-ETF abnormal TOM return 0.6% → 0.1% → 0.0% across halves and quarters of the sample (CXO, T3, UNVERIFIED) | **NOT SUPPORTED** (stepping aside loses the non-TOM premium) | **MIXED** | 12 | ~decades at the decayed size |
| 6 | (i) Halloween / "Sell in May" | Nov-Apr beats May-Oct in 37 countries (Bouman & Jacobsen 2002) | Holds with ~14 more years: about **10 pp** higher Nov-Apr returns on average across 37 countries (Andrade et al., FAJ 2013, UNVERIFIED). US-specific size not read. Data-snooping critique (Sullivan, Timmermann & White 2001) | **NOT SUPPORTED** (out of equities half of each year) | **MIXED** | 1 | ~decades to a century (one bet a year) |
| 7 | (d) Overnight vs intraday | 1993-2006: overnight returns 0.028-0.048%/day vs intraday −0.028 to +0.002% for S&P 500 stocks (Cliff, Cooper & Gulen, UNVERIFIED). Futures 2004-2018: the whole premium accrues around the European open (Bondarenko & Muravyev, JFQA 2023) | SPY 1993-2020 net of spreads "held" but "statistical robustness … low"; it beat BH in **64% / 61%** of rolling 1/3-year windows before the dot-com bust and **34% / 32%** after (Alpha Architect 2020, T2, UNVERIFIED). Live: NSPY **−6.9%** vs S&P +22%, Jun 2022-Jul 2023, then liquidated (Bloomberg / WealthManagement, T2) | **NOT SUPPORTED** (live failure; large tax drag) | **INSUFFICIENT** (the gross pattern is documented; there is no net, after-tax, post-2020 evidence) | 252 a year, but the intraday return being dropped is what matters | vs SPY: decades (daily TE ~13%/yr) |
| 8 | (h) Leveraged ETF with a trend filter | 2x S&P TR above its 200-day average, else T-bills: Sharpe **0.51** vs **0.30**, 1928-2015, **ignoring switching costs** (Gayed & Bilello 2016 via CXO, T3, UNVERIFIED) | No independent OOS test found. SEC/FINRA: returns beyond one day "can differ significantly" from the stated multiple (T1). LETF compounding is path-dependent (Cheng & Madhavan 2009) | **INSUFFICIENT** as edge. Higher expected return comes from leverage, not from the rule | **INSUFFICIENT** (reduces to the trend filter's MIXED gain, minus LETF costs) | as rank 3 | as rank 3 |
| 9 | (f) Sector and factor-ETF rotation | Industry momentum (Moskowitz & Grinblatt 1999); factor momentum (Ehsani & Linnainmaa, JF 2022) | Industry momentum "negligible post-decimalization", 0.09%/mo t 0.34, 2001-2021 (SH-3, verified there). A 187-factor study: factor momentum "weak at the individual factor level" and does not beat long-only (UNVERIFIED) | **NOT SUPPORTED** (sector); **INSUFFICIENT** (factor ETFs: MTUM etc. from 2013) | NOT SUPPORTED / INSUFFICIENT | 12 | vs SPY: decades |
| 10 | (e) Pre-FOMC drift | S&P 500 up about 49 bp on pre-FOMC days (as cited by Hu et al. 2019), 1994-2011 (Lucca & Moench, JF 2015, UNVERIFIED) | "Essentially disappeared after 2015" (Kurov, Wolfe & Gilbert, FRL 2021, UNVERIFIED). No drift Apr 2011-Dec 2017 (Ben Dor & Rosa, UNVERIFIED). Conflict: ~40 bp on press-conference meetings to 2018 (Lucca & Moench, Liberty Street 2018, UNVERIFIED) | **NOT SUPPORTED** (and in market only ~8 days a year) | **NOT SUPPORTED** after 2015 | 8 | at the original 49 bp: ~2.5 yr; at ~0: never |
| 11 | (e) Pre-holiday | Lakonishok & Smidt 1988; Ariel 1990 | S&P 500 premium significant 1963-1982 only; 1983-2019 t = **0.93** (Ko & Yang, CFR, UNVERIFIED). Gone for large firms 1987-1996 (Vergin & McGinnis 1999, UNVERIFIED) | **NOT SUPPORTED** | **NOT SUPPORTED** | ~9 | never |
| 12 | (g) Treasury term-premium / carry timing | Forward-rate and yield-curve predictors of bond excess returns (Cochrane-Piazzesi tradition) | Forward rates do "not generate systematic economic value"; they "do not outperform the no-predictability benchmark" OOS (Thornton & Valente, RFS 2012, UNVERIFIED). Predictive evidence "much weaker than it originally appeared"; only level and slope robust (Bauer & Hamilton, RFS 2018, UNVERIFIED). Credit timing: not searched | **NOT SUPPORTED** (a bond sleeve is also below SPY's expected return) | **NOT SUPPORTED** (Treasury timing); **INSUFFICIENT** (credit) | 12 | never |

## Evidence

### Benchmarks: what "beats" means here
- **Return (the owner's objective): buy-and-hold SPY total return.** A rule that holds SPY only part of the time, with T-bills or bonds otherwise, has an expected return of roughly `f × E[SPY on the held periods] + (1 − f) × E[alternative]`. It beats SPY only if the skipped periods earn less than the alternative. Every verified US-index study in the repo found equal or lower return (TT-10). Zakamulin's out-of-sample mean is 20% lower (TT-2). Growth of $100 over 1930-2012 was 64,345 for SMA OOS against 166,155 for buy-and-hold (trend-timing report, Table 4A row).
- **Risk-adjusted: a volatility-matched SPY/T-bill mix**, the fair yardstick for a rule with lower volatility; a 60/40 is the practitioner version. Worked from verified figures:
  - **Clare et al. (in-sample, TT-5).** Trend 10.50% at 10.57% vol; buy-and-hold 10.54% at 14.65%, Sharpe 0.39. The implied cash rate is 10.54 − 0.39 × 14.65 ≈ 4.83%. A static mix with w = 10.57/14.65 = 0.72 in SPY returns 0.72 × 10.54 + 0.28 × 4.83 ≈ **8.94%**. So in-sample the trend rule beats the vol-matched mix by about **1.6 pp/yr** and ties SPY. (This is our arithmetic; Clare's cash rate is UNVERIFIED, as the trend-timing report noted.)
  - **Zakamulin (out of sample, TT-2).** Sharpe +7%, std −32%. The rule's excess return ≈ 1.07 × 0.68 = 0.73 of buy-and-hold's excess return; the vol-matched mix earns 0.68. That is a gap of about 0.05 × the equity premium ≈ **+0.3 pp/yr** at a 6% premium. Zakamulin reports changes in mean and std, not excess returns, so this is an approximation.

### (a) Time-series momentum across asset classes
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net? |
|---|---|---|---|---|---|
| TSMOM is positive across 58 futures; diversified long/short Sharpe > 1 | MOP 2012 (TT-9, verified in the 2026-09-25 report) | 1 | "Sharpe ratio greater than one", gross | in-sample 1985-2009 | No |
| Asset by asset it is weak; S&P 500 R²_OS −1.78% | Huang et al. 2020 (TT-6, verified) | 1 | 8 of 55 significant slopes | OOS 2000-2015 | No |
| Trend is weak for years after a crisis | Hutchinson & O'Brien, "Is This Time Different?" (UCC WP 2014) | 1 | post-crisis returns "less than half" of non-crisis returns, for ~4 years; SG CTA Trend Sub-Index −0.8%/yr Jan 2009-Jun 2013 vs 8.0% over the prior 5 years; no positive net excess return in the last 52 months of the sample (UNVERIFIED, abstract plus secondary). Supported by Aspect Capital | partly | net excess (index) |
| The 2010s were weak because markets made fewer large moves | Babu, Hoffman, Levine, Ooi, Schroeder & Stamelos, JPM 2020 (AQR) | 1/2 (manager-affiliated) | "no evidence of a material change in trend-following strategies' ability to translate market moves" (UNVERIFIED, summary) | yes (post-MOP) | hypothetical |
| 2022 was a record year | SG Trend Index via trade press | 2 | +27.35% in 2022, "highest since launch in January 2000" (UNVERIFIED) | live index | net of fees |

Transfer: a long-only ETF version (rotate among SPY, EFA, TLT, GLD, DBC, or go to cash) cannot short. It becomes GTAA (candidate b) and gives up the short-futures legs that made 2022's gain. A managed-futures ETF (e.g. DBMF, KMLM; tickers named for tooling status only, Tier 3) holds futures inside the fund, so the account needs no shorting. Its return against SPY over 2009-2021 was far below SPY's (UNVERIFIED; no fund record read).

### (b) Dual momentum and tactical asset allocation
- Faber's 2013 update says the models "achiev[ed] equity like returns with bond like volatility and drawdowns" in real time. It was not read; secondary only, UNVERIFIED. A secondary source says the timing model underperformed buy-and-hold in all four years 2009-2012 (UNVERIFIED).
- Alpha Architect (2017, T2): TAA "has underperformed … the US 60/40", by about **4.3%/yr** since the market bottom. It adds that 60/40's strength "arguably benefits from 20/20 hindsight" (UNVERIFIED, search summary).
- The Cambria GTAA ETF lagged in "five straight years, and in eight of the last 11" (Canadian Couch Potato, T3). It bore "little resemblance" to the paper's rules, so this is tooling-status context only.
- Antonacci GEM: the only independent review found (Investresolve, T3) reports that OOS results "held up reasonably well" over 1950-73 and 2012-18, but the 12-month spec "regressed to the median". No Tier 1 OOS test was found (UNVERIFIED).
- Marmi et al. (2013, International Journal of Computational Economics and Econometrics) is an academic critique of Faber's TAA that recommends bootstrap tests (abstract only, UNVERIFIED).

### (c) Volatility targeting and volatility-managed portfolios
- **For.**
  - Moreira & Muir (JF 2017): market alpha 4.9%, appraisal ratio 0.33, Sharpe +25% (NBER w22208 snippet, UNVERIFIED).
  - Harvey et al. (JPM 2018): for equities, vol targeting lowers the probability of extreme returns. The Sharpe ratio rises from about 0.40 to about 0.48-0.51 (secondary, UNVERIFIED), and vol of vol falls from 4.6% to 1.8%.
  - Barroso & Detzel (JFE 2021): the managed *market* portfolio's abnormal returns "are profitable after transaction costs", but only "when sentiment is high". For other factors, management "generally produces zero abnormal returns and significantly reduces Sharpe ratios" (abstract, UNVERIFIED).
- **Against.**
  - Cederburg, O'Doherty, Wang & Yan (JFE 2020), 103 equity strategies: realistic OOS versions "generally earn lower certainty equivalent returns and Sharpe ratios than do simple unmanaged investments". The cause is "structural instability" in the spanning regressions (abstract, UNVERIFIED).
  - A look-ahead critique (author attribution unconfirmed, UNVERIFIED): Moreira-Muir's scaling constant used full-sample volatility. With rolling 5-20-year windows, maximum drawdowns were 68-93%.
  - DeMiguel, Martín-Utrera & Uppal (JF 2024) summarise the state: Cederburg et al. "show that these strategies fail out-of-sample", and Barroso & Detzel "show they do not survive transaction costs".
- **For the owner's objective.** Capped at 1x, which a cash account requires, a vol-targeted SPY holds less than 100% SPY on average, so its expected return is below SPY's. Above 1x it needs margin or a leveraged ETF, and the charter's "no leverage" clause applies (cited in [beyond-long-only](2026-10-10-beyond-long-only.md), step 2).

### (d) Overnight vs intraday
- **Gross pattern.** Cliff, Cooper & Gulen (2008), S&P 500 stocks 1993-2006: overnight 0.028-0.048%/day, intraday −0.028 to +0.002%, with lower overnight volatility (secondary, UNVERIFIED). Lou, Polk & Skouras (JFE 2019) document a "tug of war" between overnight and intraday clienteles (not read).
  - Bondarenko & Muravyev (JFQA 2023), E-mini futures 2004-2018: four hours around the European open account "for the entire average market return". The appendix notes that waiting "does not reduce trading costs for small trades", since the spread is almost always one tick (abstract and appendix snippets, UNVERIFIED).
  - Boyarchenko, Larsen & Whelan (NY Fed SR 917) attribute the drift to dealer inventory (not read).
- **Net and post-publication.** Alpha Architect (2020, T2), SPY 1993-Jan 2020 net of historical spreads and $0.01/share: the edge "held gross and net", but robustness was low, "more of a random walk than a repeatable trading strategy". It beat buy-and-hold in 64% / 61% of rolling 1- and 3-year windows before the dot-com bust and 34% / 32% after (search summary, UNVERIFIED).
  - Aretz & Bartram (global): day returns dominate in 1993-1998; night returns are larger in 8 of 13 years 1999-2011 (UNVERIFIED).
- **Live record (T2, financial press; SEC 497 filing T1).** NightShares 500 (NSPY) launched June 2022 and was down 6.9% total return against +22% for the S&P 500. It held $3.7M at closure. The board decided on **2023-07-14**; closing date 2023-07-31, liquidation 2023-08-10 (SEC Form 497). Morningstar's Ben Johnson: such strategies often "don't adequately account for real-world implementation costs". One fund's 14 months is weak evidence about the effect, but it is the only live test.
- **Costs at our size.** SPY's spread is one cent on a price of several hundred dollars, a fraction of a basis point (our arithmetic; SPY's cost under the rung-1 rule has not been measured, FC-3 review). Spread is not the binding cost. The binding costs are:
  - **auction access.** Probe 3: a 1-share OPG order expired unfilled 4 of 4 times on paper (ADR 0005 amendment);
  - **the 24/5 session's limit-only rule** (below);
  - **taxes** (below).

### (e) Calendar effects
- **Pre-FOMC.**
  - Lucca & Moench (JF 2015; NY Fed SR 512): pre-FOMC S&P 500 returns "averaged to a statistically significant 20 basis points" in the working paper's framing. Hu, Pan, Wang & Zhu cite about 49 bp on pre-FOMC days. The drift is weak before September 1994 (UNVERIFIED, snippets; the window definition differs between versions).
  - Kurov, Wolfe & Gilbert (FRL 2021): the drift "essentially disappeared after 2015", for meetings with and without press conferences, "explained by reduced uncertainty" (SSRN abstract, UNVERIFIED).
  - Conflict: Lucca & Moench's Liberty Street update (2018-11-26) finds about 40 bp on press-conference meetings only, from the open the day before to lunchtime on the announcement day (UNVERIFIED).
  - Every FOMC meeting has had a press conference since 2019 (general knowledge, UNVERIFIED), so the conflict matters. The two most recent independent sources say the drift is gone.
- **Turn-of-month.**
  - McConnell & Xu (FAJ 2008), CRSP 1926-2005: investors "received no reward for bearing market risk except at turns of the month" (UNVERIFIED).
  - Against: Maberly & Waggoner (Atlanta Fed WP 2000) find TOM gone in S&P 500 futures and spot after 1990 (UNVERIFIED).
  - CXO (T3) finds the TOM window on the S&P 500 averaged 0.68% against 0.24% for all eight-day intervals, 1928-2024. Across nine Select Sector SPDRs the abnormal TOM return fell from 0.6% to 0.1% to 0.0% (UNVERIFIED, paywalled summary).
  - Etula et al. (RFS 2020) tie the cycle to settlement-driven cash demand. Their US sample ends in 2013 (FC-4 review, verified there).
- **Pre-holiday.** Ko & Yang (CFR): significant for the DJIA and S&P 500 in 1963-1982, not in 1983-2019 (t 0.64 and 0.93). Vergin & McGinnis (1999): gone for large firms in 1987-1996 (UNVERIFIED).
- **Data snooping.** Sullivan, Timmermann & White (J. Econometrics 2001) tested about 9,500 calendar rules on 100 years of data: calendar effects "no longer remain significant" when judged against the full universe (abstract, UNVERIFIED).

### (f) Sector and factor rotation
- Industry momentum is settled in the repo: NOT SUPPORTED after 2001 (SH-3; Dai et al. 0.09%/mo, t 0.34, 2001-2021; CZ post-publication 0.06, t 0.18). This report does not reopen it.
- Factor momentum: Ehsani & Linnainmaa (JF 2022) find that most factors are positively autocorrelated and that factor momentum "explains all forms of individual stock momentum". A 187-factor follow-up finds the effect "weak at the individual factor level", and its strategies "do not outperform the corresponding long-only strategies" (UNVERIFIED, snippets).
- Factor ETFs start late (MTUM 2013, UNVERIFIED date), and their pre-launch index histories are backfilled (Data, below).

### (g) Treasury and credit timing
- Thornton & Valente (RFS 2012): forward-rate predictors give no OOS economic value to a dynamic allocator (abstract, UNVERIFIED).
- Bauer & Hamilton (RFS 2018): "serious small-sample distortions"; only level and slope are robust; unspanned macro evidence is "much weaker than it originally appeared" (abstract, UNVERIFIED).
- Pettenuzzo, Gargano & Timmermann are cited as finding no higher OOS Sharpe from forward spreads (UNVERIFIED).
- Credit-spread timing through HYG/LQD: not searched within budget (INSUFFICIENT).
- For the return objective, any bond sleeve lowers expected return against SPY.

### (h) Leveraged-ETF rules
- Gayed & Bilello ("Leverage for the Long Run", 2016 Dow Award paper) find a Sharpe of 0.51 for 2x leverage against 0.30 for buy-and-hold, 1928-2015. CXO's review says the backtest "assumes a 1% annual leverage cost, ignores switching costs, and uses an index rather than a liquid fund". It is in-sample only (T3 review, UNVERIFIED).
- The SEC/FINRA Investor Alert (T1, investor.gov) says leveraged and inverse ETF performance over more than one day "can differ significantly from their stated daily performance objectives". FINRA RN 09-31: "typically are unsuitable for retail investors who plan to hold them for more than one trading session".
- Cheng & Madhavan (JOIM 2009): returns are path-dependent, with daily re-leveraging near the close (abstract, UNVERIFIED).
- **Our arithmetic, standard approximation.** A daily-reset L-times fund loses about (L² − L)/2 × σ² a year against L times the index's compound return, before fees and financing. At σ = 18%: about 3.2%/yr for 2x and about 9.7%/yr for 3x. A trend filter lowers average σ while invested, which is why these backtests look good, but it does not remove the drag in the volatile regimes it misses.
- **Bottom line.** A trend-filtered LETF beats SPY in expected return through leverage. Its risk-adjusted edge is the trend filter's (rank 3, MIXED), less LETF fees, financing and decay. It also opens the charter's "no leverage" question (owner).

## Number of bets and the forward exam (ADR 0016, amendments 2026-11-06/07)
- **The bounds.** The bounds are t ≥ 4.762 / 3.367 / 2.750 at 12 / 24 / 36 months, with t ≈ IR × √years. So the net IR needed against the benchmark is about **4.8** at 12 months, **2.4** at 24 and **1.6** at 36.
- **Against SPY return,** every candidate's expected excess is ≤ 0, so the exam's futility rules would most likely stop it at month 6 or 12.
- **Against a vol-matched mix,** the best plausible IRs (our estimates) are:
  - trend overlay: ~0.04 OOS to ~0.2 in-sample;
  - vol targeting and TOM: similar.

  At IR 0.2, t = 2 takes (2/0.2)² = 100 years; at 0.04, about 2,500. A 36-month forward exam has essentially no power for any rule in this report, consistent with ADR 0005's "~44 years" for an excess Sharpe of 0.3 (HO-11).
- **Independent bets.** Monthly rebalances are not independent bets. For trend, GTAA and vol targeting the effective sample is the number of bear markets or high-volatility regimes, about four in 80 years for US trend (TT-3). The daily overnight trade has 252 observations a year, but each is a noisy draw on a small mean: the dropped intraday return. That needs decades at a daily standard deviation near 0.8% (UNVERIFIED level).

## Data: free, point-in-time-safe long histories and their pitfalls
| Need | Free source | Pitfalls |
|---|---|---|
| US market total return, monthly/daily, 1926 on | Ken French Data Library (Mkt-RF plus RF) | Rebuilt from CRSP each release, so values can change between vintages (not point-in-time; size UNVERIFIED). Value-weighted CRSP, not the S&P 500. Fine for index-level timing tests; store the vintage date as `known_at` |
| S&P Composite, 1871 on | Shiller's online data | Prices are **monthly averages of daily closes**, which induce spurious autocorrelation and flatter trend and momentum timing; dividends are interpolated. Use end-of-month values where possible (pitfall known from the literature; not re-read here, UNVERIFIED) |
| T-bill, Treasury yields | FRED (DTB3, DGS10, etc.) | Revisions are rare for market series; `known_at` = the publication date. Some ICE/BofA credit series on FRED have restricted history (UNVERIFIED) |
| ETF bars | Alpaca SIP from 2016 (store); only **SPY** of the ETFs is in `listings` today (FC-3 review) | ETF inception dates cap real history (from memory, UNVERIFIED: SPY 1993, EFA 2001, IEF/TLT 2002, AGG 2003, GLD 2004, DBC 2006, BIL 2007, Select Sector SPDRs 1998, MTUM 2013, SGOV 2020). Earlier "ETF" history is an index stitched in, with no fees or tracking error |
| Index histories before an ETF's launch | Index provider factsheets | **Backfilled**: factor, risk-control and commodity indices are often back-calculated at launch, which is in-sample by construction. Leveraged-ETF "simulated" histories assume financing at T-bills and no tracking error |
| Adjusted closes | Vendor `adj_close` | Back-adjusted with today's factors, so levels change after each dividend. An MA signal on vendor adjusted closes differs from one computed at the time. Build total return forward from raw closes plus dividends keyed to the ex-date `known_at`, as the store's corporate-actions path already does for SPY |

## Tax drag and Alpaca constraints
- **Turnover by candidate (UNVERIFIED estimates).**
  - Trend, GTAA, dual momentum, leveraged trend: about 1-4 round trips a year per sleeve.
  - Vol targeting: 12 small rebalances a year.
  - TOM: 12 round trips.
  - Pre-FOMC: 8.
  - Halloween: 1.
  - Overnight: ~252.
- **What every rule realizes.** Each rule realizes gains that buy-and-hold SPY defers. Gains on positions held a year or less are taxed as ordinary income (HO-20). Losses on whipsaw exits followed by re-entry within 30 days are disallowed and added to the replacement's basis (G7-1, G7-3). That defers the loss rather than destroying it, but it still costs.
- **Order of magnitude (our arithmetic).** A rule that realizes its full return each year at a 24% marginal rate, against buy-and-hold deferral, gives up roughly 0.24 × the realized gain. At about 8-10% a year, that is about **2 pp/yr**, before state tax.
- **Overnight.** Every gain is short-term, and dividends are very likely non-qualified because the holding is too short (IRS holding-period rule, not re-read: UNVERIFIED). Daily losing nights re-bought the next close are wash sales.
- **Swapping SPY for IVV or VOO.** Swapping SPY for another S&P 500 ETF to dodge a wash sale has no IRS ruling and is contested (Fidelity, Kitces, Morningstar: T2, UNVERIFIED).
- **Alpaca.**
  - **Shorting.** No candidate needs it in a long-only form. Multi-asset TSMOM with shorts would need margin of at least $2,000 (LS-16), or a managed-futures ETF instead.
  - **Fractional shares.** Supported, DAY only (HO-13).
  - **Extended hours.** Limit orders only, DAY or GTC; market and stop orders are refused (verified on the sandbox, [2026-10-19-alpaca-sandbox-after-hours](2026-10-19-alpaca-sandbox-after-hours.md)).
  - **Overnight session.** The 24/5 session (20:00-04:00 ET) is limit-only. The docs disagree on GTC and on fractional orders, and eligibility is per asset (`overnight_tradable`) (Alpaca docs, T1, UNVERIFIED in detail).
  - **Auctions.** A close-to-open trade needs MOC/LOC and MOO/OPG fills; Probe 3 saw OPG 1-share orders expire 4 of 4 times on paper.
  - **Cash-account settlement.** T+1 since 2024-05-28 (FC-4 card) may produce good-faith violations on daily round trips. Whether #813 is a cash or a limited margin account is an open question ([beyond-long-only](2026-10-10-beyond-long-only.md), follow-ups). Not checked here.
  - **Leveraged ETFs.** Whether Alpaca adds an acknowledgement for leveraged ETFs: UNVERIFIED.

## If the owner amends the objective: the cleanest single registered test
This is offered because the brief asks for it. It is not a recommendation to amend (an owner decision under ADR 0005; #659 rejected the same trade-off on 2026-10-03).
- **Rule (frozen):** hold SPY total return when its month-end total-return index is above its 10-month simple average, otherwise T-bills. Signal at the XNYS month-end close, fill at the next session's open. Costs at the measured SPY cost, with a stress rung. One parameter set, no variants (k = 1).
- **Benchmark (frozen before the test):** a static SPY/T-bill mix whose SPY weight equals the rule's volatility ratio, estimated **only on pre-test data**. Report buy-and-hold SPY return beside it, always (ADR 0005).
- **Data and window:** Ken French Mkt + RF monthly (one vintage, `known_at` its download date). Calibrate on 1927-1989, test on 1990-2015; leave the store window and everything after the development boundary untouched (ADR 0016). Report Zakamulin-style results across split points, because the verdict flips with the split (TT-3).
- **Pre-declared outcome:** Sharpe and terminal-wealth difference against the vol-matched mix, with a bootstrap interval; and the return gap against SPY, which is expected to be negative.
- **What it can show:** only whether the known +0 to +7% Sharpe gain is there in this split. Its information value is low, because Zakamulin 2014 (TT-2) has already run nearly this test. A forward exam cannot add power (see above).

## Disconfirmation
- **Searches run (30 total; most were aimed at disconfirmation):**
  - Cederburg et al. OOS;
  - Moreira-Muir look-ahead / Liu-Tang-Zhou;
  - Barroso-Detzel costs;
  - Harvey et al. vol targeting;
  - Kurov-Wolfe-Gilbert pre-FOMC disappearance;
  - Lucca-Moench original and the 2018 update;
  - Bondarenko-Muravyev;
  - NightShares closure;
  - overnight net of costs and its decay;
  - Cliff-Cooper-Gulen;
  - Faber GTAA since 2009, twice;
  - Antonacci GEM OOS;
  - TOM persistence;
  - Sullivan-Timmermann-White;
  - pre-holiday decay;
  - Halloween OOS (Andrade et al.);
  - AQR "You can't always trend";
  - SG Trend 2009-2019 vs 2022;
  - Hutchinson-O'Brien post-crisis trend;
  - Gayed-Bilello critique;
  - LETF trend OOS;
  - Cheng-Madhavan;
  - SEC/FINRA LETF alert;
  - Thornton-Valente;
  - Bauer-Hamilton;
  - factor momentum;
  - wash sales on same-index ETFs;
  - Alpaca 24/5.
- **What was found against each core claim:**
  - Pre-FOMC: gone after 2015 (two sources), with one conflicting update.
  - Overnight: low robustness after 2001, and a failed live ETF.
  - Vol management: no OOS gain across 103 strategies, and costs kill all but the market.
  - GTAA: trails 60/40 after 2009.
  - Trend: weak for years after crises, and the 2010s were poor.
  - Pre-holiday: gone after 1983.
  - TOM: gone in futures after 1990 by one study, and decaying in sector ETFs.
  - Bond timing: no OOS economic value.
  - Calendar rules generally: data-snooping.
  - Leveraged trend: in-sample only, costs ignored.
- **What was found for:**
  - Multi-asset trend's 2022.
  - The Halloween effect's OOS survival (Andrade et al.).
  - The vol-managed market surviving costs (Barroso & Detzel).
  - Lucca & Moench's 2018 update.
  - McConnell & Xu's long-run TOM.

  None of these is evidence of beating SPY's *return* in a long-only cash account.

## Caveats & gaps
- **Verification.** No full text was read in this pass. Every figure from a new source is search-level. The grades rest on the direction of several independent sources plus the structural argument, which is why confidence is medium-high on direction and medium-low on magnitudes.
- **Benchmarks against post-boundary data.** Any return series running past 2023-12-29 (SG Trend 2022 is inside it; NSPY 2022-23 is inside it) is context, never out-of-sample evidence for a TradePartner trial (ADR 0016).
- **A 60/40 is not volatility-matched to every rule.** The vol-matched SPY/T-bill mix is the cleaner yardstick; 60/40 also carries a bond-duration bet.
- **The structural argument has an exception.** If a skipped period's mean return were below T-bills (McConnell & Xu's pre-2005 TOM result implies that for non-TOM days), stepping aside could match SPY's return at lower exposure. The post-1990 evidence says that no longer holds.
- **Not covered in this pass.** Credit-spread timing (HYG/LQD), Koijen-Moskowitz-Pedersen-Vrugt carry, and live managed-futures ETF records.

## UNVERIFIED items
- All figures attributed to Moreira & Muir; Cederburg et al.; Barroso & Detzel; the look-ahead critique (authors unconfirmed); Harvey et al.; Lucca & Moench (both versions); Hu et al.'s 49 bp; Kurov et al.; Ben Dor & Rosa; Bondarenko & Muravyev; Cliff, Cooper & Gulen; Aretz & Bartram; the Alpha Architect overnight and TAA figures; McConnell & Xu; Maberly & Waggoner; CXO's TOM figures; Ko & Yang; Vergin & McGinnis; Sullivan, Timmermann & White; Andrade et al.; Hutchinson & O'Brien; Babu et al.; the SG Trend 2022 figure; Gayed & Bilello; Cheng & Madhavan; Thornton & Valente; Bauer & Hamilton; Ehsani & Linnainmaa and the 187-factor follow-up; Faber's 2009-2012 years; the GEM review.
- ETF inception dates (from memory); the FRED ICE-series restriction; the Shiller averaging pitfall as stated; the IRS dividend holding-period rule; Alpaca 24/5 TIF and fractional details; Alpaca leveraged-ETF acknowledgement; daily SPY intraday standard deviation of ~0.8%.
- The NSPY figures come from Bloomberg / WealthManagement (T2) and the SEC 497 filing (T1, dates only); the filing itself was not opened.
- All "IR" and "years to noise" figures are this report's arithmetic on the stated inputs.

## Follow-up questions (not answered here)
1. Is #813 a cash or a limited margin account, and would daily round trips in it raise good-faith violations under T+1?
2. What do SPY's own paper fills cost under the rung-1 rule? FC-3 and FC-4 were rejected on the single-stock figure.
3. Does the charter's "no leverage" clause cover holding a leveraged ETF in a cash account? (Owner.)
4. Credit-spread and cross-asset carry timing through ETFs: one targeted pass, if the owner wants (g) closed.
5. A verification pass on the five sources that carry the grades (Cederburg et al., Barroso & Detzel, Kurov et al., Hutchinson & O'Brien, Andrade et al.) once the hosts are reachable.

## Sources
All accessed 2026-11-20. Tier per handoff §6.1. "Read" means search result or abstract only, unless noted.
1. Moreira, A., & Muir, T. (2017). Volatility-Managed Portfolios. *JF* 72(4). NBER w22208: https://www.nber.org/papers/w22208 (T1)
2. Cederburg, S., O'Doherty, M., Wang, F., & Yan, X. (2020). On the performance of volatility-managed portfolios. *JFE* 138(1) 95-117. https://econpapers.repec.org/RePEc:eee:jfinec:v:138:y:2020:i:1:p:95-117 ; WP https://www.lehigh.edu/~xuy219/research/COWY.pdf (fetch failed) (T1)
3. Barroso, P., & Detzel, A. (2021). Do limits to arbitrage explain the benefits of volatility-managed portfolios? *JFE* 140(3) 744-767. https://econpapers.repec.org/RePEc:eee:jfinec:v:140:y:2021:i:3:p:744-767 (T1)
4. DeMiguel, V., Martín-Utrera, A., & Uppal, R. (2024). A Multifactor Perspective on Volatility-Managed Portfolios. *JF*. https://lbsresearch.london.edu/id/eprint/3716 (T1)
5. Harvey, C., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., & Van Hemert, O. (2018). The Impact of Volatility Targeting. *JPM* 45(1). https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf (T1)
6. Lucca, D., & Moench, E. (2015). The Pre-FOMC Announcement Drift. *JF* 70(1); NY Fed SR 512: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr512.pdf (T1)
7. Lucca, D., & Moench, E. (2018-11-26). The Pre-FOMC Announcement Drift: More Recent Evidence. Liberty Street Economics. https://libertystreeteconomics.newyorkfed.org/2018/11/the-pre-fomc-announcement-drift-more-recent-evidence.html (T1, official)
8. Kurov, A., Wolfe, M., & Gilbert, T. (2021). The Disappearing Pre-FOMC Announcement Drift. *Finance Research Letters* 40. https://ideas.repec.org/a/eee/finlet/v40y2021ics1544612320315956.html ; WP https://www.skidmore.edu/economics/documents/KurovWolfeGilbert-TheDisappearingPre-FOMC-Announce-Drift-200914.pdf (T1)
9. Hu, G., Pan, J., Wang, J., & Zhu, H. (2022). Premium for heightened uncertainty. *JFE*; NBER w25817: https://www.nber.org/system/files/working_papers/w25817/revisions/w25817.rev0.pdf (T1)
10. Bondarenko, O., & Muravyev, D. (2023). Market Return Around the Clock: A Puzzle. *JFQA* 58(3) 939-967. https://ideas.repec.org/a/cup/jfinqa/v58y2023i3p939-967_1.html (T1)
11. Boyarchenko, N., Larsen, L., & Whelan, P. The Overnight Drift. NY Fed SR 917. https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr917.pdf (T1, not read)
12. Cliff, M., Cooper, M., & Gulen, H. (2008). Return Differences between Trading and Non-Trading Hours: Like Night and Day. Via https://www.cxoadvisory.com/calendar-effects/buy-at-the-close-and-sell-at-the-open (T1 paper; T3 summary)
13. Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. *JFE*. https://personal.lse.ac.uk/polk/research/TugOfWar.pdf (T1, not read)
14. Alpha Architect (2020-06-16). Trading Costs Wipe Out the Overnight Return Anomaly. https://alphaarchitect.com/trading-costs-wipe-out-the-overnight-return-anomaly/ (T2; fetch failed, search summary only)
15. Bloomberg / WealthManagement (2023-07-19). 'Night Effect' ETFs to Shut Down With Overnight Stock Returns Elusive. https://www.wealthmanagement.com/etfs/night-effect-etfs-shut-down-overnight-stock-returns-elusive (T2); etf.com https://www.etf.com/sections/news/2-nightshares-etfs-close-after-struggling-gain-traction (T2)
16. Unified Series Trust, Form 497 (NightShares liquidation), SEC EDGAR. https://www.sec.gov/Archives/edgar/data/1199046/000158064223003676/nightshares497s.htm (T1, dates via search)
17. McConnell, J., & Xu, W. (2008). Equity Returns at the Turn of the Month. *FAJ* 64(2). https://ideas.repec.org/a/taf/ufajxx/v64y2008i2p49-64.html (T1)
18. Maberly, E., & Waggoner, D. (2000). Closing the question on the continuation of turn-of-the-month effects. Atlanta Fed WP 2000-11. https://www.atlantafed.org/research/publications/wp/2000/11.aspx (T1)
19. CXO Advisory, turn-of-month summaries. https://www.cxoadvisory.com/?p=4100 (T3)
20. Ko, K., & Yang, ? Pre-holiday premium. *Critical Finance Review*. https://cfr.ivo-welch.info/published/papers/ko2021pre.pdf (T1; co-author's first name not confirmed)
21. Vergin, R., & McGinnis, J. (1999). Revisiting the holiday effect. *Applied Financial Economics* 9(5). https://ideas.repec.org/a/taf/apfiec/v9y1999i5p477-482.html (T1)
22. Sullivan, R., Timmermann, A., & White, H. (2001). Dangers of data mining: the case of calendar effects in stock returns. *J. Econometrics* 105(1) 249-286. https://researchonline.lse.ac.uk/id/eprint/18482 (T1)
23. Andrade, S., Chhaochharia, V., & Fuerst, M. (2013). "Sell in May and Go Away" Just Won't Go Away. *FAJ* 69(4) 94-105. https://ideas.repec.org/a/taf/ufajxx/v69y2013i4p94-105.html (T1)
24. Babu, A., Hoffman, B., Levine, A., Ooi, Y. H., Schroeder, S., & Stamelos, E. (2020). You Can't Always Trend When You Want. *JPM*. https://www.aqr.com/Insights/Research/Journal-Article/You-Cant-Always-Trend-When-You-Want (T1/T2, manager-affiliated)
25. Hutchinson, M., & O'Brien, J. (2014). Is This Time Different? Trend Following and Financial Crises. UCC WP. https://www.ucc.ie/en/media/research/centreforinvestmentresearch/wp/TrendFollowingFeb2014.pdf (T1)
26. SG Trend Index 2022 (trade press). https://thefullfx.com/year-end-drop-fails-to-dampen-stellar-2022-for-trend-followers/ (T2)
27. Alpha Architect (2017-04-27). Tactical asset allocation vs US 60/40. https://alphaarchitect.com/tactical-asset-allocation-us-6040-benchmark/ (T2; search summary)
28. Faber, M. (2013 update). A Quantitative Approach to Tactical Asset Allocation. SSRN 962461 (via https://www.scribd.com/document/559580322/SSRN-id962461) (T1 WP; not read)
29. Canadian Couch Potato (2013-12-30). The failed promise of market timing. https://canadiancouchpotato.com/2013/12/30/the-failed-promise-of-market-timing/ (T3)
30. Advisor Perspectives (2019-05-06). Global Equity Momentum: A Craftsman's Perspective (Investresolve). https://api.advisorperspectives.com/articles/2019/05/06/global-equity-momentum-a-craftsmans-perspective (T3)
31. CXO Advisory on Gayed & Bilello. https://www.cxoadvisory.com/?p=28316 ; https://www.cxoadvisory.com/volatility-effects/leveraging-the-u-s-stock-market-based-on-sma-rules/ (T3)
32. Cheng, M., & Madhavan, A. (2009). The Dynamics of Leveraged and Inverse Exchange-Traded Funds. *JOIM* 7(4). https://joim.com/dynamics-leveraged-inverse-exchange-traded-funds (T1)
33. SEC/FINRA Investor Alert, Leveraged and Inverse ETFs. https://www.investor.gov/news-alerts/investor-alerts/sec-finra-investor-alert-leveraged-inverse-etfs ; FINRA RN 09-31 https://www.finra.org/rules-guidance/notices/09-31 (T1)
34. Thornton, D., & Valente, G. (2012). Out-of-Sample Predictions of Bond Excess Returns and Forward Rates. *RFS* 25(10) 3141-3168. https://ideas.repec.org/a/oup/rfinst/v25y2012i10p3141-3168.html (T1)
35. Bauer, M., & Hamilton, J. (2018). Robust Bond Risk Premia. *RFS* 31(2) 399-448. https://www.nber.org/papers/w23480 (T1)
36. Ehsani, S., & Linnainmaa, J. (2022). Factor Momentum and the Momentum Factor. *JF* 77(3). https://www.nber.org/papers/w25551 (T1); 187-factor follow-up via https://hull-repository.worktribe.com/OutputFile/4011273 (T1, unconfirmed which result is whose)
37. Alpaca docs, 24/5 Trading. https://docs.alpaca.markets/us/docs/245-trading ; Trading API FAQ https://docs.alpaca.markets/us/docs/trading-api-faqs (T1 vendor docs)
38. Fidelity, Tax rules for losses on ETFs. https://www.fidelity.com/learning-center/investment-products/etf/tax-rules-for-losses-etfs ; Kitces https://www.kitces.com/blog/the-wash-sale-problem-when-tax-loss-harvesting-almost-substantially-identical-mutual-funds-and-etfs/ (T2)
39. Repo: [2026-09-25-trend-timing](2026-09-25-trend-timing.md) (TT-1 to TT-11, verified from full text); [2026-10-10-beyond-long-only](2026-10-10-beyond-long-only.md) (LS-16, charter "no leverage"); [2026-10-09-short-horizon-candidates](2026-10-09-short-horizon-candidates.md) (SH-3); [idea-cards](idea-cards.md) FC-3/FC-4; [2026-10-19-alpaca-sandbox-after-hours](2026-10-19-alpaca-sandbox-after-hours.md); [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md); [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) (amendments 2026-11-06/07); [claims.toml](claims.toml) (HO-5, HO-11, HO-13, HO-20, G7-1, G7-3, QI-25).

## Budget note
No numeric budget was relayed with the brief. Used: 30 web searches; 2 fetches, both failed (hosts outside the session's allowed network). Per the session's network note no further hosts were tried, so a verification pass needs lehigh.edu, alphaarchitect.com, or the journals' open copies to be allowed.

## Proposed claims (for docs/research/claims.toml; prefix LE-, not edited here)
| ID | Statement (short) | Kind | Grade | Scope / note |
|---|---|---|---|---|
| LE-1 | No ETF-level timing, rotation or calendar rule has credible post-publication evidence of beating buy-and-hold SPY total return net of costs and taxes in a long-only taxable account | empirical | NOT SUPPORTED | nine candidate families, this report; magnitudes UNVERIFIED |
| LE-2 | A long-only cash rule that steps out of SPY beats SPY's return only if the skipped periods earn less than the alternative held; only leverage raises expected return above SPY | fact (arithmetic) | SUPPORTED | derived; post-1990 evidence says skipped periods earned more than T-bills |
| LE-3 | Multi-asset time-series momentum reduces portfolio drawdowns in crises (2022) but was flat to negative for years after crises (2009-2013) and in the 2010s | empirical | MIXED | Hutchinson-O'Brien; Babu et al.; SG Trend; gross/net mix; UNVERIFIED figures |
| LE-4 | Volatility-managed portfolios do not beat unmanaged ones out of sample, except possibly the market portfolio after costs | empirical | MIXED | Cederburg et al. 2020 against Barroso-Detzel 2021; abstract level |
| LE-5 | The pre-FOMC drift has disappeared since about 2015 | empirical | NOT SUPPORTED (the drift's persistence) | Kurov-Wolfe-Gilbert; Ben Dor-Rosa; conflict with Lucca-Moench 2018 (press-conference meetings) |
| LE-6 | Harvesting the overnight return in SPY beats buy-and-hold net of costs after 2001 | empirical | NOT SUPPORTED | Alpha Architect 2020 (34%/32% rolling win rate after the dot-com bust); NSPY live −6.9% vs +22%, then liquidated 2023 |
| LE-7 | The S&P 500 pre-holiday premium is gone after 1983 | empirical | NOT SUPPORTED (persistence) | Ko-Yang t 0.93 for 1983-2019; Vergin-McGinnis |
| LE-8 | The turn-of-month premium persists in US index ETFs | empirical | MIXED | McConnell-Xu against Maberly-Waggoner; sector-ETF decay (T3) |
| LE-9 | The Halloween (Nov-Apr) effect survived its first out-of-sample test internationally | empirical | MIXED | Andrade et al. 2013; data-snooping critique; US size not read |
| LE-10 | Tactical asset allocation (GTAA, dual momentum) beat a 60/40 or SPY after 2009 | empirical | NOT SUPPORTED | Alpha Architect 2017 (−4.3 pp/yr vs 60/40), T2, UNVERIFIED; no Tier 1 OOS found |
| LE-11 | A trend-filtered leveraged ETF rule has independent out-of-sample net evidence of a risk-adjusted edge beyond the unlevered trend filter's | empirical | INSUFFICIENT | in-sample only (Gayed-Bilello), costs ignored |
| LE-12 | Yield-curve predictors of Treasury excess returns give no out-of-sample economic value | empirical | SUPPORTED (provisional) | Thornton-Valente 2012; Bauer-Hamilton 2018; Pettenuzzo et al. (cited); abstract level. No source addresses costs, which can only strengthen a null; regrade after the full texts are read |
| LE-13 | Under ADR 0016's group-sequential bounds a forward exam needs a net IR of about 4.8 / 2.4 / 1.6 at 12 / 24 / 36 months, far above any ETF rule's plausible IR (≤ 0.2 against a vol-matched mix) | fact (arithmetic) | SUPPORTED | t ≈ IR√years on the pinned bounds |
| LE-14 | Against a vol-matched SPY/T-bill mix, a US 10-month trend overlay adds about +1.6 pp/yr in-sample (Clare) and about +0.3 pp/yr out of sample (Zakamulin) | empirical (derived) | MIXED | our arithmetic on TT-2 and TT-5; Clare's cash rate UNVERIFIED |
| LE-15 | Alpaca's overnight (24/5) session accepts limit orders only, with eligibility per asset; docs conflict on GTC and fractional orders | fact | UNGRADED | Alpaca docs via search, 2026-11-20 |

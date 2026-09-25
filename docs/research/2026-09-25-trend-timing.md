# Research Report: Independent evaluations of simple trend-timing rules (G3)

**Brief:** #48  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE. The done-when is met. There is a grade under handoff §6.1, and a five-line summary for the strongest supporting study (Clare et al. 2013) and the strongest disconfirming study (Zakamulin 2014). Both summaries carry table-level figures verified from full text. A few items remain UNVERIFIED (the HOP exhibit images; Clare's dividend and cash-rate treatment). I judged them non-material to the grade; see "UNVERIFIED items".  ·  **Agent/model:** research agent, team utopia, claude-opus-5-5

All sources were retrieved on **2026-09-25**. Pass 2 read eight full texts (S1, S2, S3, S4, S5, S6, S8, S9), which the main session extracted to plain text. Page citations use the extract's "=== page N" markers; where a journal page is printed, it is given too. The version read for each source is recorded under Sources.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium–high on the grade; medium on magnitudes

The independent (non-Faber) Tier 1 evidence conflicts, but the verified figures lean clearly negative for this project's case:

- **For (in-sample, or multi-asset):**
  - Clare et al. (2013) tested the S&P 500 from 1952 to 2011, net of 0.2% per trade. The end-of-month 10-month MA had a Sharpe ratio of 0.54 against 0.39 for buy-and-hold, at equal return (10.50% vs 10.54%). The result is in-sample, with no significance test [S3, Table 6].
  - MOP [S5] and HOP [S4] find time-series momentum across 55–67 futures markets and back to 1880. Their design is long/short, volatility-scaled and diversified.
- **Against (out-of-sample and/or net of costs, on US equity indices):**
  - **Zakamulin 2014.** This is an out-of-sample test with 0.50% one-way costs, on the S&P Composite from 1930 to 2012. A robust bootstrap averages across split points. The best result is SMA timing on the S&P Composite: its Sharpe ratio is only **7% higher** than buy-and-hold, with mean return **20% lower**. The same test gives the momentum rule **+0%** and SMA on the DJIA **−11%** [S1, Table 6, p. 28].
  - **Zakamulin 2018.** It shows Glabadanidis's results came from trading on the same month's return. With that corrected, MA(10) on the top-momentum decile has a Sharpe ratio of 0.62 against 0.57 (p = 0.83) [S2, Table 2].
  - **Huang et al.** For the S&P 500 futures, the 12-month signal's out-of-sample R² is **−1.78%** (2000–2015), and the trend strategy's Sharpe ratio of 0.15 does not beat the 0.16 of a strategy that needs no predictability [S8, Tables 2 and 9].
  - **Marshall et al.** On the largest CRSP size quintile, the MA rules break even only at one-way costs of **−10 to 15 bp**, against an estimated 40 bp [S6, Table 3].
  - **Bajgrowicz & Scaillet.** On the DJIA, which includes 200-day MA rules, they find "we do not detect any positive performance … already under zero transaction costs" for 1962–2011 [S9, p. 21].

**For this project's case** (long-only, monthly overlay on a large-cap US universe; ADR 0006), the best independent out-of-sample, net-of-cost estimate is a Sharpe gain of about 7% or less, with lower return and no statistical significance. The positive evidence is either in-sample (Clare) or a multi-asset long/short design that does not transfer (MOP, HOP).

**Why MIXED rather than NOT SUPPORTED.** The evidence conflicts across studies (Clare, HOP), and no credible study shows the S&P effect is strictly *gone*: Zakamulin 2014 still finds a small positive Sharpe difference for SMA on the S&P Composite. This sits near the MIXED / NOT SUPPORTED boundary. The owner should read it as "at best marginal" for a US large-cap overlay.

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Real-life (OOS, net) MA and TSMOM timing on US indices is at best marginally better than buy-and-hold | Zakamulin 2014 [S1] (SSRN WP, Nov 2013 revision) | 1 | Robust OOS vs buy-and-hold, S&P Composite, SMA(k): mean **−20%**, std **−32%**, Sharpe **+7%**. MOM(k): −21%, −30%, **+0%**. DJIA SMA: Sharpe **−11%** (Table 6, p. 28). "only 2 out of 8 tested active strategies are able to produce a slightly better performance" (p. 27) | **Yes.** k ∈ [2, 24] months chosen each month by expanding window (p. 11), averaged over bootstrap split points; 1930–2012 single split in Table 4 | **Yes:** "one-way transaction costs in the stock market amount to 0.50%, that is, λ = 0.005" (p. 10) |
| Single-split OOS 1930–2012, S&P Composite | Zakamulin 2014 [S1] | 1 | Buy-and-hold Sharpe **0.109**; SMA OOS **0.120**; MOM OOS **0.122**. Max drawdown −79.18% vs −61.68% (SMA OOS). Growth of $100: 166,155 vs 64,345 (SMA OOS) (Table 4A, p. 23) | Yes | Yes (0.50%) |
| OOS verdict flips with the split point; gains are confined to four bear markets | Zakamulin 2014 [S1] | 1 | Split Dec 1929: OOS Sharpe 0.12 vs 0.11. 1975–2012: 0.13 / 0.12 vs 0.13. 2009–2012: 0.09 / 0.12 vs 0.25 (Table 3, p. 20). Gains come from "1930-31, 1973-74, 2001-02, and 2007-08" (p. 21). Inferior "during a 25-year period from 1975 to 1999" (p. 30) | Yes | Yes |
| The full-sample best rule for the S&P Composite is SMA(10) | Zakamulin 2014 [S1] | 1 | "Standard and Poor Composite SMA(10) MOM(5)" (Table 2, p. 19). Best-in-backtest Sharpe 0.160 vs 0.109, **before costs** (Table 4A) | No (backtest) | No |
| Glabadanidis's results come from look-ahead bias; corrected MA(10) is not significantly better | Zakamulin 2018 [S2] (SSRN WP, Mar 2016 revision) | 1 | The look-ahead version earns month t's return when "Pt > MAt(L)"; the correct one earns month t+1's (p. 5). Corrected, 1960–2011, 0.5% one-way costs: top-momentum decile BH SR **0.57** vs MA(10) **0.62** (p **0.83**), return 17.5% vs 16.1%. Largest size decile BH **0.29** vs MA(10) **0.44** (p 0.26) (Table 2, p. 11). "at best … only marginally better … In statistical terms … indistinguishable" (p. 1) | Re-test of a published result | Yes (50 bp one-way, p. 3–4) |
| The MA strategy beats buy-and-hold on US decile portfolios after costs | Glabadanidis 2015 [S7] | 1 | "risk-adjusted returns of 3–7% per year after transaction costs" (abstract) | No. **Refuted by S2** (look-ahead) | Yes (per abstract) |
| An end-of-month 10-month MA on the S&P 500 raises the Sharpe ratio, not the return | Clare et al. 2013 [S3] (accepted version / SSRN WP, 10 Mar 2012) | 1 | Jan 1952–Jun 2011 "TF (10MMA)": 10.50%, vol 10.57%, **Sharpe 0.54**. Buy-and-hold: 10.54%, 14.65%, **0.39** (Table 6, p. 20). 1988–2011 end-of-month 200-day MA: Sharpe **0.58** vs **0.31** (Table 2, p. 16) | **No.** Full-sample tables over many lengths; no holdout, no significance test (pp. 6–8) | **Yes:** "20 basis points transaction cost assumed for each buy and each sell" (p. 7) |
| TSMOM in 58 futures; diversified L/S Sharpe > 1 | MOP 2012 [S5] (published JFE) | 1 | "Sharpe ratio greater than one" (p. 3/JFE 230). "All 58 … positive … 52 are statistically different from zero" (p. 9/JFE 236) | In-sample 1985–2009. Pre-sample 1966–1985 Sharpe 1.1 (p. 11/JFE 238) | **No** (the Fig. 2 Sharpe ratios are "gross") |
| Equity-index subset; single-instrument vs always long | MOP [S5] | 1 | Equity index futures, 12-month look-back / 1-month hold alpha t = **3.77** (Table 2C, p. 8/JFE 235). Against always-long: "positive alpha in 90% of the cases (of which 26% are statistically significant…)" (p. 9/JFE 236) | In-sample | No |
| TSMOM positive each decade since 1880, net of costs and 2/20 fees | HOP 2017 [S4] (published JPM) | 1 (AQR authors) | "2% management fee and a 20% performance fee subject to a high-water mark" (p. 3/JPM 16). Costs are 2012 estimates, "twice as high from 1993 to 2002 and six times as high from 1880 to 1992" (p. 14/JPM 27). "8 out of 10" 60/40 drawdowns (p. 5/JPM 18) | Yes, as a pre-sample extension; not independent of MOP | Yes (method). Net Sharpe is in an exhibit image (UNVERIFIED) |
| TSMOM is weak asset by asset; the S&P 500 shows no OOS predictability | Huang et al. 2020 [S8] (published JFE) | 1 | 55 MOP futures (nine equity indexes), Jan 1985–Dec 2015 (p. 3/JFE 776). OOS 2000:01–2015:12 (p. 5/JFE 778). Only "eight display significant regression slopes at the 10% level" of 55 (p. 5/JFE 778). **S&P 500:** slope 0.14, t 0.45, R²_OS **−1.78%**; equity-index average R²_OS −1.12% (Table 2B, p. 6/JFE 779). S&P 500 TSM Sharpe **0.15** vs TSH **0.16**, p 0.83 (Table 9, p. 16/JFE 789) | **Yes** | **No** (no cost analysis found in text) |
| Technical rules on the DJIA: nothing survives from 1962; costs cancel earlier gains | Bajgrowicz & Scaillet 2012 [S9] (UNIGE accepted version) | 1 | Universe "of STW, which consists of l = 7,846 rules", including moving averages (the "200-day moving average rule" is named, p. 11); daily DJIA, 1897–2011 (pp. 15–16). "In the three most recent sample periods (1962–2011), we do not detect any positive performance … already under zero transaction costs" (p. 21). 1897–1962: one-way costs of "16, 35 and 70 basis points" suffice to remove the outperformers (p. 21) | **Yes** (persistence tests; "not possible to select these rules ex ante", p. 25) | **Yes** |
| MA and TSMOM rules add nothing on the largest stocks after costs | Marshall et al. [S6] (SSRN WP 2225551) | 1 | "Large" = Q5 of CRSP value-weighted size quintiles, 1963–2013, daily look-backs of 10–200 days, long or T-bill (p. 11; Table 1). Q5 Sharpe: MA 0.13–0.16, TSMOM 0.07–0.14 (Table 1C, p. 30). Q5 one-way **breakeven costs**: MA 1, −8, −10, 15 bp; TSMOM −16, −20, −1, −4 bp (Table 3, p. 32), against "around 40 basis points" assumed (p. 16). "neither MA nor TSMOM strategies perform well on the large portfolio" (p. 17) | Sub-periods 1965–1986 / 1987–2013; the Q5 MA alpha is not significant recently (p. 22) | **Yes** (breakeven analysis) |
| Short-term momentum in the S&P Composite; tests have low power | Zakamulin & Giner 2022 [S10] | 1 | "compelling evidence of the presence of short-term momentum"; tests "suffer from the low power problem" (abstract) | nv | nv |

### Strongest supporting study, in five lines (Clare, Seaton, Smith & Thomas 2013 [S3], verified from full text)
1. What: MA, crossover and breakout rules on the S&P 500 (1988–2011 daily, 1952–2011 monthly), long or cash, against buy-and-hold, at 0.2% per buy and per sell (p. 7).
2. Result: the end-of-month 10-month MA has a Sharpe ratio of 0.54 against 0.39 for 1952–2011, at return 10.50% against 10.54%. The gain is volatility reduction (10.57% vs 14.65%) (Table 6, p. 20).
3. Cadence: monthly rules beat daily ones (Sharpe range 0.06–0.59 vs −0.79–0.54, p. 8). Short windows lose after costs. Stop-losses do not help (pp. 9–10).
4. Why strongest: it is independent of Faber, uses a US index, is long/flat and monthly, and is net of costs, so it is the closest supporting match to ADR 0006.
5. Limits: there is no holdout and no significance test, and the window lengths are reported full-sample. Zakamulin 2014 shows the same kind of backtest gain (Sharpe +50%) shrinking to about +7% out of sample.

### Strongest disconfirming study, in five lines (Zakamulin 2014 [S1], verified from full text, working-paper version)
1. What: SMA(k) and MOM(k) monthly timing (k = 2 to 24, chosen each month by expanding window). The underlyings are the S&P Composite and DJIA (plus bonds), 1926–2012, with switching to T-bills and 0.50% one-way stock costs (pp. 5, 10–11).
2. Robust out-of-sample result (bootstrap average over split points) against S&P buy-and-hold: SMA Sharpe **+7%**, mean return −20%. MOM Sharpe **+0%**. On the DJIA, SMA −11% and MOM −8% (Table 6, p. 28).
3. Single split, 1930–2012, S&P: Sharpe 0.120 (SMA) vs 0.109. Growth of $100 is 64,345 vs 166,155, and the 1926–2012 backtest-best rule is SMA(10) (Tables 2 and 4A, pp. 19, 23).
4. Why strongest: it is out of sample, net of realistic costs, on the US index, and explicitly targets data mining. It shows the backtest Sharpe gain of "50% to 100%" shrinking to 7% (p. 27), with gains confined to four bear markets (p. 21).
5. Limits: it is the working paper; the journal abstract says "highly overstated, to say the least", whereas the WP says "at best … only marginally better", so wording and figures may differ in print. Costs of 0.50% are high for a modern ETF. The robust result is still slightly positive for SMA on the S&P.

### How far each finding transfers to this project
The target is a long-only US equity index or portfolio overlay: go to cash when the trend is negative, rebalance monthly at the XNYS month-end with fills at the T+1 open (ADR 0006). It is benchmarked against SPY and MTUM total return (ADR 0005). It has no shorts, no leverage and no futures.

| Source | Instrument / design | Transfer |
|---|---|---|
| Zakamulin 2014 [S1] | S&P Composite and DJIA, monthly SMA/MOM, long/T-bill, 0.50% one-way, OOS | **High.** Nearly the exact candidate design. Differences: k is re-optimised monthly rather than fixed at 10, and its cost is above a modern ETF's. Signals use price, not total return, but the author says conclusions "remain intact regardless" (p. 7, fn 6). |
| Zakamulin 2018 [S2] | Monthly MA(10) and MA(24) on US value-weighted deciles by size, B/M and **momentum**, 1960–2011, 0.5% one-way | **High for a MOM-portfolio overlay.** On the top-momentum decile, MA(10) adds 0.05 Sharpe (p 0.83) and loses 1.4 pp/yr of return. |
| Huang et al. [S8] | 12-month past return predicting next month, 55 futures; S&P 500 reported separately | **Medium–high.** The S&P 500 row applies directly: R²_OS −1.78% and TSM ≈ TSH. The strategy is L/S futures and gross of costs. |
| Marshall et al. [S6] | Daily MA and TSMOM (10–200 days), long/T-bill, CRSP size quintiles 1963–2013 | **High** on universe: Q5 is the large-cap segment ADR 0006 selects, and breakeven costs are about zero. **Medium** on cadence: the rules are daily. |
| Bajgrowicz & Scaillet [S9] | 7,846 daily rules (including 200-day MA) on the DJIA, long/neutral/short, FDR plus persistence tests | **Medium.** Daily, and allows shorts. Its no-persistence and no-performance-since-1962 findings apply to picking any window. |
| Clare et al. [S3] | S&P 500, long/cash, end-of-month, 0.2% per trade | **High** in design; **low–medium** in weight (in-sample; Sharpe gain at equal return). |
| MOP [S5] / HOP [S4] | Long/short, vol-scaled, 55–67 multi-asset futures | **Low.** The Sharpe > 1 depends on diversification and shorts. MOP's own single-instrument test finds only 26% of instruments beat always-long significantly. |
| Zakamulin & Giner [S10] | TSMOM on the S&P Composite; power analysis | **High** in instrument. The effect may exist but cannot be confirmed at realistic sample lengths (ADR 0005). |

## Disconfirmation
- **Searches run:**
  - Pass 1 (5 of 12 were disconfirmation-directed):
    - Zakamulin 2018 / Glabadanidis look-ahead.
    - Huang et al.
    - Bajgrowicz & Scaillet.
    - General query: MA timing out-of-sample failure and data-snooping critique.
    - Post-2009 decay of trend following.
  - Pass 2 (5 searches): full-text routes only. No new disconfirmation searches.
- **What was found against** (now verified from full text):
  - **Out-of-sample, net of costs:** Zakamulin 2014 [S1], S&P Sharpe +7% (SMA) and +0% (MOM); DJIA negative. The out-of-sample verdict flips with the split point (Table 3).
  - **Failed replication:** Zakamulin 2018 [S2] replicates Glabadanidis only when trading on the same month's return. Corrected, no Sharpe difference is significant.
  - **No OOS predictability for the S&P 500:** Huang et al. [S8], R²_OS −1.78%, and the strategy does not beat a no-predictability benchmark.
  - **No net profit on large caps:** Marshall et al. [S6], Q5 breakeven costs of −10 to 15 bp against about 40 bp.
  - **Data mining:** Bajgrowicz & Scaillet [S9] find no positive DJIA performance since 1962 even at zero cost, and no ex-ante persistence.
  - **From the supporting papers themselves:** Clare shows no return gain; MOP shows only 26% of instruments significantly beat always-long; HOP shows weakness in high-correlation years (late 2008 to mid-2014).
  - **Post-publication decay (flag only, not fetched, UNVERIFIED):** Kurth, Eisler, Rej & Bouchaud (2026), arXiv 2607.01550. Short-term futures trend, largely out of scope.

## Caveats & gaps
- **Working-paper versions.** Three sources were read as working papers:
  - S1 (Zakamulin 2014): SSRN, Nov 2013 revision.
  - S2 (Zakamulin 2018): SSRN, Mar 2016 revision.
  - S6 (Marshall et al.): SSRN 2225551.

  The S1 journal abstract is worded more strongly than the WP abstract ("highly overstated, to say the least" vs "at best … only marginally better"). Figures in the published versions may differ. S3 is the accepted version (SSRN WP dated 10 Mar 2012). S4, S5 and S8 are published versions. S9 is the accepted version.
- **Cost levels vary.** S1 and S2 assume 0.50% one-way; Clare 0.2% per trade; Marshall ~40 bp; B&S 12.5 bp, with breakeven analysis. At today's ETF costs (a few bp), S1's +7% Sharpe could be somewhat higher. None of the verified studies suggests a large gain even before costs out of sample: S1's pre-cost gain appears only in the backtest.
- **Sharpe vs return.** Every US-index study, whether supporting or disconfirming, finds lower volatility and equal or lower return. Against the ADR 0005 total-return benchmarks, a trend overlay would be expected to show lower terminal wealth, per S1 Table 4A.
- **MOP and Huang are gross of costs.** Neither deducts transaction costs.
- **Low statistical power** [S10] means that neither a positive nor a negative result on a single US index is decisive.
- **Citation fix:** Bajgrowicz & Scaillet is JFE 106(3) 473–491, 2012 (S11); EconPapers' "European Journal of Finance" is a metadata error.
- **Failed or unusable fetches, not counted:** pass 1, 12; pass 2, 1 (the SMU PDF).

## UNVERIFIED items
Closed in pass 2, from full texts:
- **S1 (Zakamulin 2014):**
  - Index: S&P Composite and DJIA, plus two bond indices.
  - Period: 1926–2012; out of sample from 1930.
  - OOS design: k ∈ [2, 24], expanding window, plus bootstrap averaging over split points.
  - Costs: 0.50% one-way.
  - OOS Sharpe: 0.120 / 0.122 vs 0.109 (Table 4A); robust +7% / +0% (Table 6).
- **S2 (Zakamulin 2018):** the look-ahead mechanism (the month-t signal applied to the month-t return). The corrected result (Table 2), and the "marginally better / indistinguishable" wording, now quoted from p. 1.
- **S8 (Huang et al.):** universe (MOP's 55 futures, including nine equity indexes and the S&P 500). Period 1985–2015; out of sample 2000–2015.
- **S9 (Bajgrowicz & Scaillet):** yes, MA rules including 200-day are in the 7,846-rule STW universe. Exact parameterisation is deferred to STW and was not checked.
- **S6 (Marshall et al.):** "large" = the largest CRSP value-weighted size quintile. Net-of-cost figures are given as breakeven costs (Q5 about 0 bp).
- Earlier in pass 2: Clare's costs, OOS status and figures; MOP's samples, equity subset and gross status; HOP's fee and cost method; B&S's journal and sample end.

Remaining (judged **non-material to the grade**):
- **HOP [S4]:** net Sharpe (Exhibit 1), cost bps (Exhibit B1), equity-only results. These are images needing a visual read. Non-material because HOP's transfer is low and the grade does not rest on its magnitude.
- **Clare [S3]:** whether Table 6's buy-and-hold is total return, and the cash rate. Non-material to the grade, which is MIXED either way. It is material to Clare's magnitude: a price-only benchmark would flatter timing.
- **S9:** the exact MA window list in STW.
- **S1, S2, S6:** possible differences between working paper and journal versions.

## Follow-up questions (not answered here)
1. Does a market-trend overlay reduce momentum-crash losses (Daniel & Moskowitz's "panic states", handoff H1) enough to change MOM's net Sharpe? S2's momentum-decile result (0.62 vs 0.57, p 0.83) is weak evidence that it does not, for the long leg.
2. If a trend overlay is pre-registered, how many window and rule variants count as trials? S1's split-point sensitivity (Table 3) suggests the evaluation window itself is a degree of freedom.
3. Is a Sharpe gain at lower return (the pattern in S1, S3 and S2) a valid objective under ADR 0005, given total-return benchmarks? That is an owner decision.
4. Does the post-2009 decay in short-term trend (Kurth et al. 2026, flag only) extend to 10-to-12-month trend on US equity indices?

## Sources
Retrieved 2026-09-25. Tier per handoff §6.1. All are Tier 1. The version read is noted for each.

- **S1** Zakamulin, V. (2014). "The real-life performance of market timing with moving average and time-series momentum rules." *Journal of Asset Management* 15(4) 261–278. Abstract via EconPapers: https://econpapers.repec.org/RePEc:pal:assmgt:v:15:y:2014:i:4:d:10.1057_jam.2014.25. **Full text read:** SSRN 2242795 working paper, "This revision: November 8, 2013", 35 pp.; the published version may differ.
- **S2** Zakamulin, V. (2018). "Revisiting the Profitability of Market Timing with Moving Averages." *International Review of Finance*, DOI 10.1111/irfi.12132. Abstract: https://vzakamulin.weebly.com/papers1.html. **Full text read:** SSRN 2743119 working paper, "This revision: March 7, 2016", 11 pp.; the published version may differ.
- **S3** Clare, A., Seaton, J., Smith, P. N., Thomas, S. (2013). *Journal of Asset Management* 14(3) 182–194. https://openaccess.city.ac.uk/17842/. **Full text read:** City accepted version (SSRN 2126476 WP, 10 Mar 2012); it "may differ from the final published version".
- **S4** Hurst, B., Ooi, Y. H., Pedersen, L. H. (2017). *Journal of Portfolio Management* 44(1) 15–29. https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing. **Full text read:** published JPM Fall 2017 (AQR PDF); extract page N = JPM page 13+N.
- **S5** Moskowitz, T. J., Ooi, Y. H., Pedersen, L. H. (2012). *Journal of Financial Economics* 104(2) 228–250. https://research.cbs.dk/en/publications/time-series-momentum/. **Full text read:** published JFE version, https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf; extract page N = JFE page 227+N.
- **S6** Marshall, B. R., Nguyen, N. H., Visaltanachoti, N. (2017). *Quantitative Finance* 17(3) 405–421. https://ideas.repec.org/a/taf/quantf/v17y2017i3p405-421.html. **Full text read:** SSRN 2225551 working paper, 45 pp.; the published version may differ.
- **S7** Glabadanidis, P. (2015). *International Review of Finance* 15(3) 387–425. https://ideas.repec.org/a/bla/irvfin/v15y2015i3p387-425.html (abstract only; its results are re-tested in S2).
- **S8** Huang, D., Li, J., Wang, L., Zhou, G. (2020). *Journal of Financial Economics* 135(3) 774–794. https://profiles.wustl.edu/en/publications/time-series-momentum-is-it-there/. **Full text read:** published JFE version (AEF mirror); extract page N = JFE page 773+N.
- **S9** Bajgrowicz, P., Scaillet, O. (2012). *Journal of Financial Economics* 106(3) 473–491. https://econpapers.repec.org/paper/chfrpseri/rp0805.htm. **Full text read:** UNIGE accepted version, 59 pp.; "layout of the published version may differ".
- **S10** Zakamulin, V., Giner, J. (2022). *International Review of Financial Analysis* 82. https://ideas.repec.org/a/eee/finana/v82y2022ics1057521922001363.html (abstract only).
- **S11** (pass 2) Archive ouverte UNIGE record for S9: https://archive-ouverte.unige.ch/unige:79889 (repository record).

Search-snippet references (flags only, UNVERIFIED): Kurth, Eisler, Rej & Bouchaud (2026), arXiv 2607.01550: https://arxiv.org/html/2607.01550v1

## Budget note
**Pass 1** (brief budget 10 sources / 15 searches): 10 of 10 sources (S1–S10) and 12 of 15 searches, plus 12 failed fetches not counted.

**Pass 2** (owner-authorised on 2026-09-25, fresh budget 10 sources / 15 searches):
- **Used:** 1 of 10 new sources (S11) and 5 of 15 searches, plus 1 failed fetch.
- **Full-text extracts** of S1, S2, S3, S4, S5, S6, S8 and S9 were supplied by the main session and read. They count as the existing sources, not new ones.

**Cumulative:** 11 sources; 17 searches.

**Grade change:** none, MIXED throughout. Confidence rose from medium to medium–high, and the verified figures moved the transfer assessment toward "at best marginal" for a US large-cap overlay. Both five-line summaries now carry verified figures. Status changed from INCOMPLETE to COMPLETE.

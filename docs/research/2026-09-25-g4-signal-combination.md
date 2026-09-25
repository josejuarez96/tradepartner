# Research Report: Does combining published anomaly signals help a long-only retail portfolio? (G4)

**Brief:** #49  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE (after pass 2). The done-when is met: a §6.1 grade plus the retail statement. The gaps that remain are listed under "UNVERIFIED items" and "Caveats & gaps". None of them would move the grade off MIXED; see "Why COMPLETE" below.  ·  **Agent/model:** research agent, team finneas, claude-opus-5-5

This replaces handoff H9 (INSUFFICIENT: one source, no disconfirmation search).

- **Pass 1:** abstracts only.
- **Pass 2:** full text of S1, S3, S5 (working-paper version), S11 and S12, supplied by the orchestrator. S2 and S7 are still abstract-only.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium. Medium-high that combining beats a single signal in long-short, net-of-cost tests. Low on the size of any gain for a long-only large/mid-cap book.

**For a long-short investor, combining is better than one signal, and this holds out-of-sample, net of costs.**
- S1 uses a rolling-window, net-of-cost test from 1980 to 2014. A 51-characteristic portfolio reaches an out-of-sample Sharpe ratio of 1.356, against 0.675 for size/value/momentum and 1.072 for size/value/investment/profitability (S1 Table 5).
- Combining cuts the marginal trading cost of each characteristic by about 65% (S1 §6).

**Under a no-short constraint, the evidence weakens.**
- S1's short-sale-constrained version beats the value-weighted market, but the difference is "not statistically significant" (S1 Internet Appendix IA.12.2).
- Its gains over small-signal portfolios are significant only in the smallest 60% of stocks (IA.12.1).
- The one direct long-only, net-of-cost test is S11, a Tier 2 AQR backtest over 1993–2015 in developed large caps. An integrated value+momentum portfolio had an information ratio of 0.87, against 0.44 for momentum alone and 0.21 for value alone (S11 Exhibit 5). This was not tested out of sample.

**The absolute edge after publication and in the modern era is small, and it decays most in liquid stocks.**
- The average cost-optimized anomaly nets 8 bps/month after publication and after 2005 (S5 WP, Table 2).
- Non-microcap predictability collapsed after 2003 (S2).
- Post-publication decay is larger for large, liquid, low-idiosyncratic-risk portfolios (S12 §3.5).
- Published signals become correlated with each other after publication (S12 Table 9), which erodes the diversification that combining relies on.

**Multi-signal backtests carry a large overfitting bias.** Using the best 3 of 20 signals is as biased as using the single best of 1,250 (S3 Table 1).

## Evidence
Full text was read for S1, S3, S5 (WP), S11 and S12; the rest are abstract-only. Section, table and exhibit numbers are the authors' own. The markdown conversions carry no reliable page numbers, so none are given.

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Many characteristics beat few, out of sample and net of costs (long-short, unconstrained) | S1 DeMiguel et al., RFS 2020, author version, §7.2 and Table 5 | 1 | Out-of-sample net Sharpe: Regularized (51 chars) **1.356**; Size/val./inv./prof. **1.072**; Size/val./mom. **0.675**; VW **0.567**; 1/N **0.482**. Monthly turnover 0.979 / 0.963 / 0.754 / 0.050 / 0.134. "achieve an out-of-sample Sharpe ratio that is 100% higher than … three characteristics and 25% higher than … four" (§7.2) | Yes: rolling 100-month estimation window, 319 out-of-sample months within Jan 1980–Dec 2014 (§7.1). Not post-publication | Yes: proportional costs (§3.2) |
| Cost model and universe | S1 §2, §3.2 | 1 | Costs "in the 1980s of about 180 basis points for the smallest firms and 100 basis points for the largest firms, and after 2002 of about 60 basis points for the smallest firms and 35 basis points for the largest firms" (§3.2). Firms "below the 20th percentile of market capitalization" removed (§2). Robust to daily-data cost estimates and to quadratic (price-impact-style) costs (IA.1, IA.2) | n/a | n/a |
| Why combining helps: trades cancel | S1 §6 | 1 | Jointly significant characteristics rise "from five in the absence of transaction costs to 15"; the marginal transaction cost of the 15 "is reduced by around 65% on average when they are combined". Individually (Bonferroni), 21 are significant without costs but "only 14" with them (§6; IA.13) | In-sample | Yes |
| Turnover had to be capped | S1 fn. 33; IA.12.3 | 1 | Unscaled portfolios "result in very large turnovers" (~386%/month). The main results are "scal[ed] … so that the portfolio monthly turnover is around 100%". Even unscaled, the net Sharpe is ~125% above the 3-characteristic portfolio (IA.12.3) | Yes | Yes |
| **Long-only: gain not significant** | S1 IA.12.2 (Table IA.31) | 1 | "with shortsale constraints, although the out-of-sample Sharpe ratio of the regularized parametric portfolios is higher than that of the value-weighted benchmark portfolio, the difference is not statistically significant". With ~50% shorting it beats the 3- and 4-characteristic portfolios by ~48% and ~22%. The long-only portfolio is formed by zeroing negative weights and renormalising (fn. 11). Table IA.31 values were not in the supplied file | Yes | Yes |
| **Gain concentrated in smaller stocks** | S1 IA.12.1 (Table IA.30) | 1 | Beats VW "for the first four quintiles corresponding to the 80% of smallest stocks"; beats the small-characteristic portfolios only "for the first three quintiles corresponding to the 60% of the smallest stocks" | Yes | Yes |
| Overfitting check on the 51-characteristic result | S1 IA.12.4 (Table IA.33) | 1 | The 51-characteristic portfolio "does not significantly outperform" a portfolio using the 15 in-sample-significant characteristics estimated on past data only | Yes | Yes |
| Predictability collapsed after 2003 outside microcaps | S2 Green, Hand & Zhang, RFS 2017 (abstract only) | 1 | "12 characteristics are reliably independent determinants in non-microcap stocks from 1980 to 2014"; "just two … since" 2003; hedge returns "insignificantly different from zero since 2003" | Yes (sub-period) | No |
| Size of the overfitting bias | S3 Novy-Marx, NBER WP 21329, §1 and Table 1 | 1 (WP) | Best 3 of 20 signals ≈ single best of **1,250** candidates (Table 1, Panel A, equal-weighted); best 5 of 50 ≈ **900,000** (the text says "almost as bad as … one million"). Signal-weighted (Panel B): 1,810 and 1.55×10⁶. §1: random-signal constructions "backtest, in real data, with t-statistics in excess of five, and statistical significance at the 5% level requires t-statistics in excess of seven" | n/a (bias result) | n/a |
| S3 is not against combining itself | S3 §1 | 1 (WP) | "these results do not suggest that strategy performance cannot be improved by combining multiple signals … one should not believe in a combination of signals simply because they backtest well together"; "the marginal contribution of each individual signal should be evaluated individually" | n/a | n/a |
| The single-signal hurdle is t > 3 | S4 Harvey, Liu & Zhu, RFS 2016 (abstract only) | 1 | "a t-statistic greater than 3.0" | n/a | No |
| Anomalies are small post-publication and post-2005, net of spreads | S5 Chen & Velikov, **FEDS WP 2020-039** (May 2020), Figure 1, Table 2 | 1 (WP of JFQA 2023) | 120 long-short anomalies, cost-optimized: gross in-sample 66, net in-sample **38**, net post-pub **13**, net post-pub & post-2005 **8** bps/month (Table 2 Panel B). Value-weighted only: **4** (Panel C) | Yes | Yes (effective spreads; "omit … price impact and short-sale fees") |
| Handoff's "5 bp to 38 bp" re-checked | S5 WP, Table 2; §2.3.2 | 1 (WP) | **Verified.** Panel A (academic equal-weighted quintiles), in-sample net = **5**; Panel B (cost-optimized), in-sample net = **38**. "optimization improves in-sample net returns by 33 bps per month, leading to a noteworthy 38 bps net return". Buy/hold spreads and weighting are chosen "to maximize the average in-sample net return" | In-sample | Yes |
| Selecting the best anomalies in advance gives 10–20 bps | S5 WP, Table 3 | 1 (WP) | Top quartile by in-sample net Sharpe: **21.2** bps (s.e. 6.2) post-pub & post-2005, equal-weighting allowed; value-weighted only **11.4** (7.0). Intro: "Restricting … to value-weighting implies expected returns of 11 bps per month, at best" | Yes | Yes |
| Size/B/M/momentum individually after 2005 | S5 WP, Table 5 | 1 (WP) | Net 2006–2016: size **−33.1** (s.e. 33.8), B/M **24.5** (29.1), momentum **12.6** (60.5) bps/month | Yes | Yes |
| **The WP version does not test combinations** | S5 WP, §1 | 1 (WP) | "A limitation of our study is that we do not allow for combining multiple anomalies." The combination figure appears only in the JFQA abstract (204 anomalies): "Several methods for combining anomalies net around 20 bps"; average anomaly "4 bps" | JFQA: yes | JFQA: yes |
| Most anomalies do not replicate | S6 Hou, Xue & Zhang, RFS 2020 (abstract only) | 1 | "65% of the 452 anomalies … cannot clear … 1.96" | Replication | No |
| Long-only: how you blend matters | S7 Ghayur, Heaney & Platt, FAJ 2018 (abstract/summary only) | 1 | "portfolio blending generates higher information ratios for low-to-moderate levels of tracking error" | Not stated | Not stated |
| Long leg share of anomaly profits; value weak in large caps | S8 Israel & Moskowitz, JFE 2013 (abstract only) | 1 | "long positions make up almost all of size, 60% of value, and half of momentum profits"; value "weak among the largest stocks" | 86 years | Partly |
| Buy/hold spread; turnover under 50% survives | S9 Novy-Marx & Velikov, RFS 2016 (abstract only) | 1 | "one-sided monthly turnover lower than 50% continue to generate statistically significant net spreads" | No | Yes |
| Cost-blind comparisons favour high-cost factors | S10 Detzel, Novy-Marx & Velikov, JF 2023 (abstract only) | 1 | "biasing tests in favor of those employing high-cost factors" | Not stated | Yes |
| **Long-only multi-style beats single style, net of estimated costs** | S11 Fitzgibbons, Friedman, Pomorski & Serban, J. Investing 26(4) 2017, Exhibit 5 Panel A | **2** (practitioner journal; all authors at AQR, which sells multi-style products) | Excess return vs MSCI World / IR: value alone 1.7% / **0.21**; momentum alone 3.4% / **0.44**; portfolio mix 2.5% / **0.62**; integrated 3.6% / **0.87**, at ~4.1% tracking error. Figures are "net of estimated transaction costs but gross of management fees" | **No**: historical backtest, Feb 1993–Dec 2015, "roughly the MSCI World benchmark universe", monthly rebalance | Yes (estimated; method not given) |
| Integration beats mixing at every tracking error | S11 Exhibit 6 Panel B | 2 | Integrated vs mix IR: "12% (1.33 versus 1.49) at 1% TE"; "36% (0.64 versus 0.87) at 4% TE"; "49% for 6% TE" | No | Yes |
| Trade netting is a small benefit | S11 "Turnover Netting" | 2 | Netting saves "5% one-sided for portfolios with annual turnover of around 100% and 10% one-sided for turnover unconstrained portfolios"; "transaction cost savings to be an order of magnitude lower than the alpha capture gains" | No | Yes |
| Post-publication decay (author working-paper version) | S12 McLean & Pontiff, author WP (82 characteristics), abstract and §3.2 | 1 (WP) | "average out-of-sample decay due to statistical bias is about 10%, but not statistically different from zero. The average post-publication decay … is about 35%". §3.2: in-sample long-short 42.8 bps/month decays by 17.3 bps post-publication | Yes | No |
| Decay is larger in liquid, large-stock portfolios | S12 §3.5–3.6 (Tables 7–8) | 1 (WP) | "Characteristic portfolios that on average consist of larger stocks, stocks with smaller bid ask spreads, and stocks with high dollar volume decline more"; idiosyncratic risk is "the only factor that has a significant effect" once all are included | Yes | No |
| Published signals co-move after publication | S12 §3.7 (Table 9) | 1 (WP) | Mean pairwise correlation "0.050"; "multi-characteristic investing is likely to enjoy substantial diversification benefits". But after publication, the slope on other published characteristics is "0.399 (p-value = 0.00)" | Yes | No |
| Published JF 2016 figures | S12 JF 71(1): 5–32 (search snippet only) | 1 | "97 variables"; returns "26% lower out-of-sample and 58% lower post-publication": **UNVERIFIED** (Wiley returned 403) | Yes | No |

### §6.1 check
Two claims are graded separately. The brief asks about the second.

- **Claim A.** Combining beats a single signal out of sample, net of costs, in long-short or unconstrained portfolios.
  - Sources: S1 (Tier 1, out-of-sample, net), S5 JFQA (Tier 1 abstract, post-publication, net) and S11 (Tier 2, net).
  - That meets the SUPPORTED bar: ≥3 sources, ≥2 of them Tier 1, ≥1 out-of-sample, ≥1 net of costs.
  - Weak points: S5's combination figure is abstract-only, and S1's out-of-sample window is not post-publication.
- **Claim B, the brief's question.** The same holds for a long-only retail portfolio of liquid large/mid caps.
  - The evidence conflicts:
    - For: S11 (Tier 2, backtest only).
    - Weak or null: S1 IA.12.2 (not significant vs VW) and S1 IA.12.1 (gains confined to smaller stocks).
    - Against: S2, S5 and S12 (the post-2003 edge is near zero and decays most in liquid stocks).
  - S7 (abstract) conflicts with S11 on whether blending portfolios or signals is better at low tracking error.
  - **MIXED**, under the "conflicting evidence" and "costs consume the effect" criteria.

### Why COMPLETE
None of the remaining gaps would change the grade:
- The JFQA combination methods (S5).
- S2 and S7 are abstract-only.
- The Table IA.31 values are missing.
- The published McLean–Pontiff numbers are unverified.

The best possible outcome on any of them would strengthen Claim A. Claim B would still rest on one Tier 2 in-sample backtest against a null Tier 1 long-only result.

## Disconfirmation
- **Pass 1 searches** (targeted against combination gains):
  1. Novy-Marx multiple-signal overfitting (found S3).
  2. "composite anomaly strategy out-of-sample net of transaction costs gains disappear…" (returned ML composites, out of scope).
  3. Detzel–Novy-Marx–Velikov (found S10).
  4. "multifactor long-only … failed to outperform" (snippets only).
- **Pass 2:** full-text reading aimed at disconfirmation inside the supplied papers:
  - S1's short-sale, size-quintile and reality-check appendices.
  - S5's own "limitation" statement.
  - S11's notes on when integration underperforms.
  - S12's cross-sectional decay results.
  - Searches: (5) McLean–Pontiff JF 2016 published figures; (6) Chen–Velikov JFQA combination methods (snippets only, no method detail).
- **What was found against:**
  - S1: under a short-sale constraint the gain is not significant (IA.12.2), and the gain over small signal sets is confined to the smallest 60% of stocks (IA.12.1).
  - S5 WP: the paper did not test combinations. The average post-publication, post-2005 net return is 8 bps (4 bps value-weighted), and the WP says short-sale fees of 10–20 bps "would wipe out the remaining profits" of the long-short average (§1).
  - S12: decay is biggest for liquid, large-stock portfolios, and published signals become correlated after publication.
  - S11 (against itself): integration "can be expected to underperform the mix when the styles themselves disappoint", and the mix may outperform in some periods ("Conclusion").
  - S3: the bias magnitudes above.
  - Not found: no Tier 1 source shows combination doing *worse* than a single signal net of costs.

## Caveats & gaps
- **Long-only is the weak link.** The only positive long-only, net-of-cost evidence (S11) is:
  - (a) a Tier 2 source whose authors sell integrated multi-style products;
  - (b) an in-sample historical backtest, not an out-of-sample test;
  - (c) a developed-market universe, not US-only;
  - (d) institutional in scale, with an unspecified cost model.

  S11 footnote 6 claims three styles "doubles the excess returns and the information ratio", but the results are "available from the authors upon request" and were not seen.
- **S1's out-of-sample test is not post-publication.** Its characteristics come from the Green–Hand–Zhang list, most of them published before or during 1980–2014, so its out-of-sample returns include pre-publication periods, which S12 shows decay afterwards. It also needed a turnover cap at ~100%/month (fn. 33), far above S9's <50% one-sided survival threshold for single anomalies.
- **S1 excludes only the bottom 20% by market cap.** Its gains concentrate in smaller stocks (IA.12.1). The charter's liquid large/mid-cap universe sits where S1's gain is weakest.
- **S5 figures come from the FEDS WP (120 anomalies, May 2020), not the JFQA article (204 anomalies).** The two differ: the average is 8 bps in the WP vs 4 bps in the JFQA abstract. The "~20 bps for combinations" appears only in the JFQA abstract; the WP explicitly does not combine anomalies (§1).
- **S12 is an early author working-paper version** (82 characteristics, ~35% post-publication decay). The published JF 2016 figures (97 predictors; 26% / 58%) come from a search snippet and are UNVERIFIED. The direction agrees across versions; the magnitudes do not.
- **Does decay compound across a combination?** S12 does not test combined portfolios. What it shows is (i) each component decays post-publication and (ii) published components become more correlated with each other (Table 9). Together these imply a combination of published signals keeps less of its in-sample diversification. That is an inference, not a tested result.
- **S11 table labels.** Exhibit 6 series labels in the supplied conversion are partly "unlabelled". The integrated vs mix figures are taken from the article text.
- **S3 table labels.** S3's text calls Table 1 Panel A "signal-weighted", but the table header says Panel A is equal-weighted and Panel B signal-weighted. The table header is followed here.

## UNVERIFIED items
- The JFQA version of S5: which combination methods give "around 20 bps", and their individual figures (the Cambridge PDF could not be parsed; the search returned no method detail).
- Table IA.31 values (S1's long-only Sharpe ratios); only the prose was in the supplied file.
- McLean & Pontiff JF 2016 published figures (97 predictors, 26% out-of-sample, 58% post-publication): search snippet only.
- S2 construction of "hedge returns" (combined forecast or single characteristics): abstract only.
- S7 universe, sample, costs and out-of-sample status: abstract only.
- S3's journal publication status.

## Candidate Phase 3 inputs, for the owner to decide
These are options the evidence bears on, not recommendations.
1. **Single vs multi-signal first.**
   - Evidence:
     - The long-short, net-of-cost benefit of combining is SUPPORTED (Claim A).
     - For long-only large/mid caps it is MIXED: S1's long-only version is not significant, and S11's positive result is a Tier 2 in-sample backtest.
   - Options:
     - (a) Single-signal MVP first.
     - (b) Pre-register one 2-signal variant (for example momentum + value or profitability) as a secondary test against the single signal.
     - (c) Defer until the MVP has run.
2. **How many signals.**
   - S3: best 3 of 20 ≈ best 1 of 1,250; best 5 of 50 ≈ 900,000.
   - S2: 2 independent non-microcap characteristics after 2003.
   - S1: its gains come from 15–51 characteristics, but mainly in smaller stocks and with shorting.
   - The owner could cap k at a small number chosen before any backtest and log every candidate in the trial registry, so that n is on record.
3. **Weighting approach.**
   - Options: equal or risk-balanced fixed weights (S11 uses 50/50 value/momentum), or fitted weights (S1 uses cross-validated lasso). S3 says freedom to weight signals "severely exacerbate[s]" the bias.
   - Integration method:
     - S11 (Tier 2): integrate signals into one composite score beats mixing single-signal portfolios at every tracking error.
     - S7 (Tier 1, abstract): the opposite at low-to-moderate tracking error.
   - These conflict, so the choice could be made a registered test rather than an assumption.
4. **Turnover controls.**
   - S9: a buy/hold spread; one-sided monthly turnover under 50%.
   - S1: capped turnover (~100%/month) still worked long-short.
   - S11: netting trades across signals saves 5–10% one-sided a year, an order of magnitude less than the alpha-capture effect.
   - Possible config items: hold band, turnover cap, netting across signals.
5. **Significance hurdle.** t > 3 (S4) for any single signal. For combinations, S3 critical values, which can exceed t = 7 in its random-signal constructions.
6. **Realistic expectation setting.**
   - Post-publication, post-2005 net figures:
     - 8 bps for the average anomaly (4 value-weighted) (S5 WP Table 2).
     - 11–21 bps for the best, selected in advance (S5 WP Table 3).
     - About 20 bps for combinations (S5 JFQA abstract).
   - Individual size/B/M/momentum net returns after 2005 have standard errors of 30–60 bps (S5 WP Table 5).
   - Any go/no-go threshold could be set against these magnitudes and judged on the holdout.

## What a retail long-only investor can and cannot expect
**Can expect.** A retail long-only investor holding liquid US large and mid caps can reasonably expect a few published signals combined into one score to:
- trade less than the same signals run side by side (S1 §6; S11 "Turnover Netting");
- in backtests, produce a higher information ratio than either signal alone. In S11's net-of-cost developed-market backtest the integrated value+momentum portfolio's information ratio was 0.87, against 0.44 for momentum alone.

**Cannot expect a reliable, statistically detectable edge.**
- The strongest out-of-sample, net-of-cost evidence for combining (S1) weakens to "not statistically significant" once shorting is forbidden.
- S1's gains come mainly from smaller stocks than this investor would hold.
- Post-publication, post-2005 net returns are single-digit bps a month for the average anomaly (S5 WP) and about 20 bps for combinations, on long-short books (S5 JFQA abstract).
- Decay is largest in exactly the liquid stocks a retail account trades (S12).
- Every added signal, and every degree of freedom in weighting, inflates the backtest well beyond what live trading will show (S3).

## Follow-up questions (not answered here)
1. A US-only, long-only, post-2003, net-of-cost, independent (non-vendor) test of a 2–3 signal composite vs its best single component in large/mid caps. None was found in either pass.
2. The JFQA version of Chen & Velikov: which combination methods net ~20 bps, and do any of them survive value-weighting?
3. Does integrating signals beat mixing portfolios (S11 vs S7) in a US large/mid-cap universe at the tracking error a retail account would run?
4. Does an ML composite change the answer? Out of scope here.

## Sources
Retrieved 2026-09-25.

**Pass 1** (abstract pages):
- **S1** DeMiguel, Martín-Utrera, Nogales & Uppal (2020), "A Transaction-Cost Perspective on the Multitude of Firm Characteristics", *RFS* 33(5): 2180–2222. https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2180-2222..html (Tier 1). **Pass 2 full text:** LBS author version, https://lbsresearch.london.edu/id/eprint/1124/1/DeMiguel_TransactionCostPerspective.pdf
- **S2** Green, Hand & Zhang (2017), *RFS* 30(12): 4389–4436. https://ideas.repec.org/a/oup/rfinst/v30y2017i12p4389-4436..html (Tier 1; abstract only)
- **S3** Novy-Marx (2015), "Backtesting Strategies Based on Multiple Signals", NBER WP 21329. https://www.nber.org/papers/w21329 (Tier 1 WP). **Pass 2 full text:** https://www.nber.org/system/files/working_papers/w21329/w21329.pdf
- **S4** Harvey, Liu & Zhu (2016), *RFS* 29(1): 5–68. https://ideas.repec.org/a/oup/rfinst/v29y2016i1p5-68..html (Tier 1; abstract only)
- **S5** Chen & Velikov (2023), "Zeroing In on the Expected Returns of Anomalies", *JFQA* 58(3): 968–1004. https://econpapers.repec.org/article/cupjfinqa/v_3a58_3ay_3a2023_3ai_3a3_3ap_3a968-1004_5f2.htm (Tier 1). **Pass 2 full text (WP version, FEDS 2020-039, May 2020):** https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf. All table figures quoted above are from the WP.
- **S6** Hou, Xue & Zhang (2020), "Replicating Anomalies", *RFS* 33(5): 2019–2133. https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html (Tier 1; abstract only)
- **S7** Ghayur, Heaney & Platt (2018), *FAJ* 74(3). https://rpc.cfainstitute.org/research/financial-analysts-journal/2018/faj-v74-n3-5 (Tier 1 peer-reviewed practitioner journal; summary only)
- **S8** Israel & Moskowitz (2013), *JFE* 108(2): 275–301. https://ideas.repec.org/a/eee/jfinec/v108y2013i2p275-301.html (Tier 1; abstract only)
- **S9** Novy-Marx & Velikov (2016), *RFS* 29(1): 104–147; NBER WP 20721. https://www.nber.org/papers/w20721 (Tier 1; abstract only)
- **S10** Detzel, Novy-Marx & Velikov (2023), *JF* 78(3): 1743–1775. https://pure.psu.edu/en/publications/model-comparison-with-transaction-costs/ (Tier 1; abstract only)

**Pass 2** (new sources):
- **S11** Fitzgibbons, Friedman, Pomorski & Serban (2017), "Long-Only Style Investing: Don't Just Mix, Integrate", *Journal of Investing* 26(4), Winter 2017, AQR copy. https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/JOI-LongOnlyStyleInvesting_Winter-2017.pdf (**Tier 2**: a practitioner journal, and all four authors are AQR employees; AQR is listed as Tier 2 in handoff §6.1. Full text.)
- **S12** McLean & Pontiff, "Does Academic Research Destroy Stock Return Predictability?", author working-paper version (82 characteristics), https://www.fmg.ac.uk/sites/default/files/2020-08/Jeffrey-Pontiff.pdf (Tier 1 WP; full text). Published as *Journal of Finance* 71(1): 5–32 (2016); citation confirmed at https://ideas.repec.org/a/bla/jfinan/v71y2016i1p5-32.html (no abstract on that page).

**Failed fetches (not counted):**
- Pass 1: the LBS PDF (binary), SSRN 2912819 and 2262374 (403), the FEDS PDF (binary). The orchestrator later supplied these as markdown.
- Pass 2: Wiley JF abstract for S12 (403).

## Pass 1 note
10 of 10 sources; 9 of 15 searches. All read at abstract level.

## Pass 2 note
The owner approved a fresh budget of 10 sources and 15 searches. Full texts were supplied by the orchestrator (Firecrawl → markdown).

**Used in pass 2:** 6 of 10 sources and 2 of 15 searches.
- Full text: S1, S3, S5 (WP), S11, S12.
- Citation page: S12 on RePEc.

**Cumulative:** 12 distinct sources; 11 searches.

**Closed in pass 2:**
- S1: the gain is out-of-sample (rolling 100-month window, 319 months) and net of proportional size- and time-varying costs, with quadratic costs as a robustness check. The long-only variant exists and its gain is not significant vs VW. Gains concentrate in smaller stocks.
- S5: the handoff's 5 → 38 bps in-sample figure is verified (WP Table 2, Panels A/B). Per-method combination figures could **not** be closed: the WP does not combine anomalies.
- S3: bias magnitudes quantified (Table 1; the §1 t > 7 critical value).
- S11: long-only, net-of-cost multi-style vs single-style evidence added (Tier 2, in-sample).
- S12: post-publication decay figures (WP version) and cross-sectional decay pattern. The compounding question is answered by inference only.

**Verdict change:** none (MIXED). Confidence went from "medium/low" to "medium" overall, with Claim A now meeting the SUPPORTED bar. Status went from INCOMPLETE to COMPLETE.

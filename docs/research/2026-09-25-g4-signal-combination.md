# Research Report: Does combining published anomaly signals help a long-only retail portfolio? (G4)

**Brief:** #49  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE. The grade and the retail statement are done. All 10 sources were read **at abstract level only**, because full-text PDFs could not be parsed and SSRN returned 403. No Tier 1 source tests *long-only, net-of-cost, combination vs single signal* head to head. See "UNVERIFIED items".  ·  **Agent/model:** research agent, team finneas, claude-opus-5-5

This replaces handoff H9 (INSUFFICIENT, one source, no disconfirmation search).

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium on the direction; low on the long-only magnitude

The *relative* claim holds up. After spreads, post-publication decay and the modern trading era, combinations of anomalies net more than single anomalies: "around 20 bps" a month, against 4 bps for the average anomaly and "at best, 10 bps" for the strongest [S5]. Combining also cuts trading costs because offsetting trades net out, and with costs included, 15 characteristics are jointly significant instead of 6 [S1]. The *absolute* gain is small or nil, though. Outside microcaps, hedge returns to characteristic-based predictability have been "insignificantly different from zero since 2003" [S2]. The ~20 bp figure is for long-short portfolios and leaves out price impact [S5]. Multi-signal backtests also carry an overfitting bias that grows fast with the number of signals picked [S3]. Every figure in the net-of-cost literature read here is long-short. For long-only, the only Tier 1 evidence is indirect: the long leg carries about 60% of value profits and half of momentum profits [S8], and portfolio blending beats signal blending at low-to-moderate tracking error [S7].

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Transaction costs *increase* the number of characteristics worth combining, because trades net out | S1 DeMiguel, Martín-Utrera, Nogales & Uppal, RFS 2020 (abstract) | 1 | Costs raise significant characteristics "from 6 to 15"; "the trades in the underlying stocks required to rebalance different characteristics often cancel out" | Not stated in the abstract (UNVERIFIED) | Yes (cost model not seen) |
| Few characteristics carry independent information, and predictability collapsed after 2003 outside microcaps | S2 Green, Hand & Zhang, RFS 2017 (abstract) | 1 | 94 characteristics; "12 characteristics are reliably independent determinants in non-microcap stocks from 1980 to 2014"; "just two characteristics have been independent determinants since then"; hedge returns "insignificantly different from zero since 2003" | Yes (post-2003 sub-period) | No (gross, per abstract) |
| Combining signals inflates backtests: best-k-of-n is nearly as biased as best-of-n^k | S3 Novy-Marx, NBER WP 21329, 2015 (abstract) | 1 | "Combining the best k out of n candidate signals yields a bias almost as large as those obtained by selecting the single best of nᵏ candidate signals"; critical values "can be several times standard levels" | n/a (a bias result) | n/a |
| Single-signal hurdle must be higher than t = 2 | S4 Harvey, Liu & Zhu, RFS 2016 (abstract) | 1 | "a t-statistic greater than 3.0"; "most claimed research findings in financial economics are likely false" | n/a | No |
| Most published anomalies do not replicate once microcaps are controlled | S6 Hou, Xue & Zhang, RFS 2020 (abstract) | 1 | "65% of the 452 anomalies … cannot clear the single test hurdle of the absolute t-value of 1.96"; replicated ones have "substantially smaller economic magnitudes" (paraphrase from the abstract page) | Replication | No |
| Combinations beat single anomalies net, but everything is small | S5 Chen & Velikov, JFQA 2023 (abstract) | 1 | Average anomaly "a measly 4 bps per month"; strongest "at best, 10 bps"; "Several methods for combining anomalies net around 20 bps"; returns negligible "despite cost mitigations that produce impressive net returns in-sample and the omission of … price impact" | Yes (post-publication, modern era) | Yes (effective spreads only; no price impact) |
| Low turnover is what survives costs; a buy/hold spread is the best simple mitigation | S9 Novy-Marx & Velikov, RFS 2016 (NBER WP 20721 abstract) | 1 | "introducing a buy/hold spread … is the single most effective simple cost mitigation strategy"; anomalies "with one-sided monthly turnover lower than 50% continue to generate statistically significant net spreads … Few of the strategies with higher turnover do" | No (full-sample) | Yes |
| Ignoring costs favours high-cost factor combinations; net of costs, rankings change | S10 Detzel, Novy-Marx & Velikov, JF 2023 (abstract) | 1 | Ignoring costs "bias[es] tests in favor of those employing high-cost factors"; net of costs the FF five-factor model "has a significantly higher squared Sharpe ratio" than q-factor or six-factor models; cash-profitability variants "perform better still" | Not stated | Yes |
| For long-only, how you combine matters: blend portfolios or blend signals | S7 Ghayur, Heaney & Platt, FAJ 2018 (abstract/summary) | 1 (peer-reviewed practitioner journal) | "portfolio blending generates higher information ratios for low-to-moderate levels of tracking error. At high levels of tracking error, signal blending delivers better risk-adjusted performance" | Not stated | Not stated |
| The long leg alone captures only part of anomaly profits; value is weak in large caps | S8 Israel & Moskowitz, JFE 2013 (abstract) | 1 | "long positions make up almost all of size, 60% of value, and half of momentum profits"; "The value premium … is weak among the largest stocks. Momentum profits, however, exhibit no reliable relation with size" | 86 years US + international | "little evidence" that returns are affected by changes in trading costs over time |

**§6.1 check:**
- Evidence *for* a relative combination benefit comes from S1 and S5 (both Tier 1), with S7 as indirect long-only support. That is fewer than 3 independent sources on the exact claim.
- S5 is post-publication and net of spreads. S1 is net of costs.
- Against that: S2 (no post-2003 non-microcap hedge return) and S3/S10 (selection and cost biases), and every net figure is long-short.
- Result: **MIXED**, on the handoff's "conflicting evidence" and "costs consume the effect" criteria.

## Disconfirmation
- **Searches run** (each targeted evidence against combination gains):
  1. Novy-Marx "Backtesting strategies based on multiple signals" overfitting (found S3).
  2. "composite anomaly strategy out-of-sample net of transaction costs gains disappear multi-characteristic portfolio post-publication". This returned mainly ML-composite papers (out of scope; not read). One Tier 3 Alpha Architect page was not used.
  3. Detzel–Novy-Marx–Velikov "Model Comparison with Transaction Costs" (found S10).
  4. "multifactor long-only portfolio combining value momentum profitability out-of-sample live performance net of costs failed to outperform". Only snippets came back (DeMiguel et al. JF 2024 on volatility-managed multifactor portfolios; Tier 3 blogs). Nothing was fetched, because the source budget was spent.
  - GHZ (S2), HLZ (S4), HXZ (S6) and Chen–Velikov (S5) are themselves disconfirmation sources named in the brief.
- **What was found against:**
  - **S2:** in non-microcaps, the hedge return from a 94-characteristic Fama-MacBeth combination has been statistically zero since 2003, and only 2 characteristics remain independent.
  - **S5:** the combination edge (~20 bps/month net) exists but is small, and it excludes price impact. In-sample cost mitigation looks "impressive" and does not carry forward.
  - **S3:** combined-signal backtests are strongly upward biased. Choosing k of n signals is nearly as bad as best-of-n^k.
  - **S10:** cost-blind comparisons favour high-cost multi-factor sets.
  - **S6, S4:** most candidate inputs to a combination may be false positives to begin with.
  - Not found: no Tier 1 source said combination is *worse* than a single signal net of costs. The disconfirmation is about magnitude and bias, not direction.

## Caveats & gaps
- **Abstract-level only.** Every source was read at abstract (or journal-summary) level. No table, page or sample-construction detail was verified. The PDFs of S1 (LBS repository) and S5 (Federal Reserve FEDS version) came back as unparseable binaries, and SSRN returned 403 for S1 and S2. These fetches are not counted as sources.
- **Long-short vs long-only.** S1, S2, S5, S9 and S10 study long-short or optimal (shortable) portfolios. How much of any combination gain survives the no-short constraint for a small account is untested here. S8 suggests roughly half the momentum profit and 60% of the value profit sit on the long side, and says value is weak in the largest stocks.
- **Scale.** None of the abstracts addresses a small personal account. Price impact is omitted in S5, and a small account should face less of it. That may make the published net figures slightly conservative for this owner. This is an inference, **UNVERIFIED**.
- **The benchmark differs by paper.** S5 compares against zero, S7 compares blending methods, and S1 counts characteristics. None benchmarks against the owner's actual alternative, a single-signal long-only portfolio vs SPY.
- **Microcaps.** S2 and S6 both show much of the anomaly evidence sits in microcaps, which the charter universe (liquid large/mid caps) excludes.

## UNVERIFIED items
- Whether S1's net-of-cost gains are out-of-sample, what cost model it uses, and whether it reports a long-only or short-constrained variant. Not visible in the abstract.
- Which combination methods S5 tested, and their individual net figures. Also the handoff's "5 bp to 38 bp/month" in-sample figure attributed to Chen & Velikov, which was not re-verified in this pass.
- S7's universe, sample period, turnover and cost treatment, and whether the results are out-of-sample.
- Whether S2's "hedge returns" come from the combined 94-characteristic forecast or from single characteristics. The abstract does not say.
- S3's journal publication status. The NBER page lists only the working paper.
- DeMiguel et al. (JF 2024), multifactor volatility-managed portfolios "out-of-sample and net of costs": search snippet only.

## Candidate Phase 3 inputs, for the owner to decide
These are options the evidence bears on. They are not recommendations.
1. **Single vs multi-signal first.** The relative benefit of combining is supported (S1, S5), but the absolute post-2003, large/mid-cap net edge is near zero (S2, S5). Options:
   - (a) Stay single-signal until the MVP has run.
   - (b) Pre-register a small multi-signal hypothesis alongside it.
   - (c) Register the multi-signal version as a secondary test against the single-signal one.
2. **How many signals.** S2 found only 12 independent characteristics in 1980–2014 and 2 after 2003. S3 shows the bias rises steeply with k. The owner could cap k (for example 2–3 signals chosen *before* any backtest from ones already graded, such as momentum per H1) and count every candidate in the trial registry, so that the n in "best k of n" is recorded.
3. **Weighting approach.** Options: fixed equal weights set in advance vs fitted weights. S5's note on "impressive" in-sample mitigation and S3's bias both argue that fitted weights carry more overfitting risk. For long-only, S7 suggests blending single-signal portfolios at low-to-moderate tracking error and blending signals only at high tracking error.
4. **Turnover controls.** S9: a buy/hold spread (hysteresis band) is the most effective simple mitigation, and strategies under 50% one-sided monthly turnover tend to survive costs. S1: combining nets trades across signals. Possible config inputs are a hold band, a monthly turnover cap and signal-level trade netting.
5. **Significance hurdle.** The owner could adopt t > 3 (S4) or a multiple-testing-adjusted threshold for any multi-signal backtest, since S3 says critical values "can be several times standard levels".
6. **Holdout use.** Any combination result could be judged on the untouched holdout only, given how large the in-sample vs out-of-sample gap is in S5.

## What a retail long-only investor can and cannot expect
A retail long-only investor holding liquid large and mid caps can reasonably expect a combination of a few published signals to be *cheaper to run and somewhat more robust* than any one of them. Trades partly cancel [S1], and post-publication net returns for combinations run about twice those of the best single anomaly [S5]. The investor cannot expect a sizeable or reliable edge. Outside microcaps, the net expected return of even combined anomaly portfolios is on the order of 20 bps/month for long-short books with no price impact [S5]. The non-microcap hedge return has been statistically zero since 2003 [S2]. A long-only book forgoes the short leg, which carries about 40% of value profits and half of momentum profits [S8]. The investor should also expect any multi-signal backtest to overstate live results, with the bias growing with the number of signals and the amount of fitting [S3, S5, S10]. Nothing read here tests the exact retail case (long-only, small account, monthly, net of costs, combination vs single). That gap is why the status is INCOMPLETE.

## Follow-up questions (not answered here)
1. A full-text read of S1 (out-of-sample design, cost model, any long-only variant) and S5 (per-method combination figures). Both need a PDF-capable fetch or a manual read.
2. Is there a Tier 1 long-only, net-of-cost, post-2003 test of multi-factor vs single-factor portfolios in US large/mid caps (for example the AQR "integrate, don't mix" work by Fitzgibbons et al., J. Investing 2017)? Not searched, for lack of budget.
3. How post-publication decay (McLean & Pontiff, JF 2016) compounds across the components of a combination. Not searched.
4. Does an ML composite (out of scope here) change the answer? The disconfirmation search surfaced such papers, which claim large net out-of-sample returns.

## Sources
Retrieved 2026-09-25. All read at **abstract level only** (the RePEc/EconPapers/NBER/journal abstract page).
- **S1** DeMiguel, Martín-Utrera, Nogales & Uppal (2020), "A Transaction-Cost Perspective on the Multitude of Firm Characteristics", *Review of Financial Studies* 33(5): 2180–2222. https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2180-2222..html (Tier 1)
- **S2** Green, Hand & Zhang (2017), "The Characteristics that Provide Independent Information about Average U.S. Monthly Stock Returns", *RFS* 30(12): 4389–4436. https://ideas.repec.org/a/oup/rfinst/v30y2017i12p4389-4436..html (Tier 1)
- **S3** Novy-Marx (2015), "Backtesting Strategies Based on Multiple Signals", NBER WP 21329. https://www.nber.org/papers/w21329 (Tier 1, working paper)
- **S4** Harvey, Liu & Zhu (2016), "… and the Cross-Section of Expected Returns", *RFS* 29(1): 5–68. https://ideas.repec.org/a/oup/rfinst/v29y2016i1p5-68..html (Tier 1)
- **S5** Chen & Velikov (2023), "Zeroing In on the Expected Returns of Anomalies", *JFQA* 58(3): 968–1004. https://econpapers.repec.org/article/cupjfinqa/v_3a58_3ay_3a2023_3ai_3a3_3ap_3a968-1004_5f2.htm (Tier 1)
- **S6** Hou, Xue & Zhang (2020), "Replicating Anomalies", *RFS* 33(5): 2019–2133. https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html (Tier 1)
- **S7** Ghayur, Heaney & Platt (2018), "Constructing Long-Only Multifactor Strategies: Portfolio Blending vs. Signal Blending", *Financial Analysts Journal* 74(3). https://rpc.cfainstitute.org/research/financial-analysts-journal/2018/faj-v74-n3-5 (Tier 1, peer-reviewed practitioner journal; summary page only)
- **S8** Israel & Moskowitz (2013), "The Role of Shorting, Firm Size, and Time on Market Anomalies", *Journal of Financial Economics* 108(2): 275–301. https://ideas.repec.org/a/eee/jfinec/v108y2013i2p275-301.html (Tier 1)
- **S9** Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs", *RFS* 29(1): 104–147; abstract from NBER WP 20721. https://www.nber.org/papers/w20721 (Tier 1)
- **S10** Detzel, Novy-Marx & Velikov (2023), "Model Comparison with Transaction Costs", *Journal of Finance* 78(3): 1743–1775. https://pure.psu.edu/en/publications/model-comparison-with-transaction-costs/ (Tier 1)

Failed fetches, not counted as sources: the LBS PDF of S1 (unparseable binary), SSRN 2912819 and 2262374 (HTTP 403), and the Federal Reserve FEDS 2020-039 PDF of S5 (unparseable binary).

**Budget used:** 10 of 10 sources; 9 of 15 searches.

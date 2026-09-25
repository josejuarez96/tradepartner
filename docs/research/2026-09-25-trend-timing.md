# Research Report: Independent evaluations of simple trend-timing rules (G3)

**Brief:** #48  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE. The grade and both five-line study summaries are delivered, but the source budget is spent (10 of 10; 12 of 15 searches). Every effect claim below rests on a verified **abstract** only: the fetch tool could not parse any full-text PDF, and SSRN, Wiley, Springer, Taylor & Francis and ResearchGate all refused the fetch. Table-level figures (Sharpe ratios, cost assumptions, out-of-sample split dates) are therefore unverified. See "UNVERIFIED items".  ·  **Agent/model:** research agent, team utopia, claude-opus-5-5

All sources were retrieved on **2026-09-25**.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium on the grade itself; low on any magnitude, because no full text was read

The independent (non-Faber) Tier 1 evidence conflicts:

- **For.** Clare et al. (2013) find that simple rules, including the 200-day MA, "dominate the long-only, passive investment" in the S&P 500, and that end-of-month rules beat more frequent trading [S3]. Glabadanidis (2015) reports "risk-adjusted returns of 3–7% per year after transaction costs" [S7]. Moskowitz–Ooi–Pedersen [S5] and Hurst–Ooi–Pedersen [S4] document time-series momentum across 58+ futures markets and back to 1880.
- **Against.** Zakamulin (2014) ran out-of-sample tests with realistic transaction costs on MA and time-series-momentum timing rules and concluded that their performance "is highly overstated, to say the least" [S1]. Zakamulin (2018) traces Glabadanidis's results to "simulating the trading with look-ahead bias" [S2]. Huang et al. (2020, JFE) find "little evidence of TSM, both in- and out-of-sample", and that the strategy's performance matches a sample-mean strategy that needs no predictability [S8]. Bajgrowicz and Scaillet find that no ex-ante rule selection persists and that "even the in-sample performance is completely offset by the introduction of transaction costs" on the DJIA [S9]. Marshall et al. (2017) find that MA and TSMOM rules "perform best outside of large stock series" [S6].

**For this project's case** (a long-only, monthly overlay on a large-cap US universe; ADR 0006), the most transferable independent out-of-sample, net-of-cost test is Zakamulin (2014), and it is negative. The positive evidence is either in-sample (Clare), disputed (Glabadanidis) or low-transfer multi-asset long/short futures (MOP, HOP). This matches the handoff's H4 grade of MIXED and adds the independent disconfirmation that H4 lacked.

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| MA and TSMOM market-timing performance is overstated once tested out of sample with costs | Zakamulin 2014, JAM 15(4) [S1] | 1 | "we perform out-of-sample tests of these two timing models in which we account for realistic transaction costs … the performance of market timing strategies is highly overstated, to say the least" (abstract) | **Yes** (OOS; design details UNVERIFIED) | **Yes** ("realistic transaction costs"; bps UNVERIFIED) |
| Glabadanidis's MA results come from look-ahead bias | Zakamulin 2018, IRF [S2] | 1 | "'too good to be true' reported performance of the moving average strategy is due to simulating the trading with look-ahead bias" (abstract). "Only marginally better" than buy-and-hold and "indistinguishable" in statistical terms: **search snippet only, UNVERIFIED** | Re-test of a published result | nv |
| The MA strategy beats buy-and-hold on US decile portfolios after costs | Glabadanidis 2015, IRF 15(3) 387–425 [S7] | 1 | "risk-adjusted returns of 3–7% per year after transaction costs"; payoff "resembles … an at-the-money protective put" (abstract) | No (in-sample; **contested by S2**) | Yes (per abstract) |
| 200-day MA and end-of-month rules dominate buy-and-hold on the S&P 500 | Clare, Seaton, Smith, Thomas 2013, JAM 14(3) 182–194 [S3] | 1 | "a range of fairly simple rules, including the popular 200-day moving average (MA) trading rule, dominate the long-only, passive investment in the index"; "monthly end-of-month investment decision rules are superior to those which trade more frequently" (abstract) | No (full-sample; UNVERIFIED) | nv (cost treatment not in abstract) |
| TSMOM exists in each of 58 futures; a diversified portfolio earns abnormal returns | Moskowitz, Ooi, Pedersen 2012, JFE 104(2) 228–250 [S5] | 1 | "significant 'time series momentum' in equity index, currency, commodity, and bond futures for each of the 58 liquid instruments"; "partially reverses over longer horizons" (abstract) | In-sample; post-publication decay not tested here | nv |
| TSMOM positive in every decade since 1880 and in most crises | Hurst, Ooi, Pedersen 2017, JPM 44(1) 15–29 [S4] | 1 (peer-reviewed practitioner journal; AQR authors) | "profitable on average since 1985 for nearly all equity index futures, fixed income futures, commodity futures and currency forwards" [S4]. "8 out of 10 of the largest crisis periods" (JPM abstract via search snippet) | Yes, as a **pre-sample** extension back to 1880; not independent of MOP | **Snippet only, UNVERIFIED:** net of trading costs and a simulated "2% annual management fee and a 20% performance fee" |
| Evidence for TSMOM is weak asset by asset, in and out of sample | Huang, Li, Wang, Zhou 2020, JFE 135(3) 774–794 [S8] | 1 | "asset-by-asset time series regressions reveal little evidence of TSM, both in- and out-of-sample"; performance "virtually the same as that of a similar strategy that is based on historical sample mean" (abstract) | **Yes** | nv |
| Technical rules on the DJIA: no persistence; costs erase in-sample gains | Bajgrowicz & Scaillet, JFE 106(3) 473–491, 2012 [S9] | 1 | "an investor would never have been able to select ex ante the future best-performing rules"; "even the in-sample performance is completely offset by the introduction of transaction costs" (working-paper abstract, DJIA 1897–2008) | **Yes** (persistence tests) | **Yes** |
| MA and TSMOM rules work least well on large stock series | Marshall, Nguyen, Visaltanachoti 2017, QF 17(3) 405–421 [S6] | 1 | "Both rules perform best outside of large stock series which may explain the puzzle of their popularity with investors, yet lack of supportive evidence in academic studies" (abstract) | nv | nv |
| Short-term momentum exists in the S&P Composite, but tests of trend profitability have low power | Zakamulin & Giner 2022, IRFA 82 [S10] | 1 | "compelling evidence of the presence of short-term momentum"; tests on the trend-following strategy's profitability "suffer from the low power problem" (abstract) | nv | nv |

### Strongest supporting study, in five lines (Clare, Seaton, Smith & Thomas 2013 [S3])
1. What: several technical rules, including the 200-day MA, tested on the S&P 500 index against long-only buy-and-hold.
2. Result: simple rules "dominate the long-only, passive investment in the index" and beat simple fundamental metrics "over the last 60 years".
3. Cadence: "monthly end-of-month investment decision rules are superior to those which trade more frequently"; stop-losses "do not add value".
4. Why strongest: it is independent of Faber, on a US index, long/flat and monthly, so it matches ADR 0006 more closely than any other supporting study.
5. Limits: the abstract reports no out-of-sample split and no cost figure (both UNVERIFIED), and "dominate" is not quantified. HOP [S4] is broader evidence for trend in general but transfers poorly (see below).

### Strongest disconfirming study, in five lines (Zakamulin 2014 [S1])
1. What: MA and time-series-momentum timing rules, the two families the brief names, applied to the US stock market (index and period UNVERIFIED).
2. Method: out-of-sample tests that account for "realistic transaction costs", aimed explicitly at the "considerable data-mining bias" in reported results.
3. Result: the performance of these rules "is highly overstated, to say the least".
4. Why strongest: it is the only fetched independent study that is both out of sample and net of costs, and it tests the same kind of rule the brief asks about.
5. Limits: the abstract gives no Sharpe or return figures, the cost assumption and split dates are unverified, and "overstated" does not mean "zero". Huang et al. [S8] (JFE) is the runner-up.

### How far each finding transfers to this project
The target is a long-only US equity index or portfolio overlay: go to cash when the trend is negative, rebalance monthly at the XNYS month-end with fills at the T+1 open (ADR 0006). It is benchmarked against SPY and MTUM total return (ADR 0005). It has no shorts, no leverage and no futures.

| Source | Instrument / design | Transfer |
|---|---|---|
| MOP [S5] | Long/short, **58 futures across four asset classes**, diversified portfolio | **Low.** The headline result is for a *diversified multi-asset* long/short portfolio. A long/flat rule on one US index gets neither the shorts nor the diversification. Only the equity-index-futures component is relevant, and the abstract gives no separate figure for it (UNVERIFIED). |
| HOP [S4] | Same design as MOP, back to 1880; costs and 2/20 fees (snippet) | **Low**, for the same reasons. Its crisis-period result ("8 out of 10") concerns a 60/40 portfolio's drawdowns hedged by a multi-asset L/S book, not a single-index exit. It is also not independent: MOP authors, AQR (a trend-fund manager). |
| Huang et al. [S8] | 12-month past return predicting next month, asset by asset (the same 12-month look-back a monthly overlay would use) | **Medium–high** on the core question of whether the signal predicts next month. Its sample includes MOP-style futures (asset list UNVERIFIED), not only US equities. |
| Zakamulin 2014 [S1] | MA and TSMOM market timing, OOS, with costs | **High** if the underlying is a broad US index (UNVERIFIED from the abstract). This is the closest match to a 10-month SMA overlay. |
| Zakamulin 2018 [S2] / Glabadanidis [S7] | Monthly MA timing on US value-weighted decile portfolios, **including momentum deciles**, and individual stocks | **Medium–high** for an overlay on a momentum portfolio, but the positive result is the one alleged to contain look-ahead bias. The individual-stock part is out of scope. |
| Clare et al. [S3] | S&P 500, 200-day MA and end-of-month rules, long-only vs buy-and-hold | **High** in design. Low in evidential weight until the out-of-sample and cost treatment are verified. |
| Bajgrowicz & Scaillet [S9] | **Daily** technical rules on the DJIA, many rules, data-snooping control | **Medium.** It is daily, not monthly, and the rule set is broader than MAs (rule list UNVERIFIED). Its data-snooping and no-persistence findings apply to *choosing* a look-back (10-month vs 200-day vs others), which this project would also do. |
| Marshall et al. [S6] | MA vs TSMOM across many series | **Medium.** The finding that the rules work least well on "large stock series" applies directly to a top-1000-by-cap universe (ADR 0006). |
| Zakamulin & Giner [S10] | TSMOM in the S&P Composite; theoretical power analysis | **High** in instrument. It supports the existence of short-term momentum in the US index, while warning that tests have low power. That is consistent with ADR 0005: the effect cannot be confirmed at this project's scale either way. |

## Disconfirmation
- **Searches run** (5 of 12 were disconfirmation-directed):
  - Zakamulin "Revisiting the profitability…" / Glabadanidis look-ahead.
  - Huang et al. "Time series momentum: Is it there?"
  - Bajgrowicz & Scaillet, false discoveries and transaction costs.
  - General query: MA timing on the S&P 500, out-of-sample failure, data-snooping critique, 10-month / 200-day rule.
  - Post-2009 decay of trend following / post-publication decline in TSMOM.
- **What was found against:**
  - **Failed replication:** Zakamulin 2018 [S2] attributes Glabadanidis's [S7] results to look-ahead bias. This is the one direct replication failure found.
  - **Out-of-sample, net of costs:** Zakamulin 2014 [S1] finds MA and TSMOM timing "highly overstated" once tested out of sample with costs.
  - **Statistical critique:** Huang et al. [S8] find the pooled TSMOM t-statistic falls below bootstrap critical values, and that the strategy's profit matches a sample-mean strategy's.
  - **Data mining and costs:** Bajgrowicz & Scaillet [S9] show that no rule selection persists ex ante and that costs offset in-sample gains (daily DJIA).
  - **Large-cap weakness:** Marshall et al. [S6] find the rules work least well on large stock series.
  - **Post-publication decay (flag only, not counted, not fetched, UNVERIFIED):** a July 2026 arXiv paper, Kurth, Eisler, Rej & Bouchaud, "Is Trend Still Your Friend? A Microstructural Account of the Demise of Short-Term Trend-Following" (arXiv 2607.01550). Per the search snippet, short-term trends have not delivered reliable returns since about 2009. It concerns *short-term* multi-asset futures trends, so it is largely out of scope.
  - The general query surfaced only Tier 3 blog backtests and a ResearchGate item. None was used.

## Caveats & gaps
- **Abstract-only evidence.** No full text could be read. Direction-of-effect claims are quoted from abstracts; no magnitude beyond Glabadanidis's "3–7% per year" is verified.
- **Few truly independent positive tests.** Of the positive sources, MOP and HOP share authors and an employer that runs trend funds. Glabadanidis is disputed. Clare et al. is the only clean independent positive, and its out-of-sample and cost treatment are unverified.
- **Multi-asset vs single-index.** The strongest and longest positive evidence (MOP, HOP) is for diversified long/short futures and does not carry over to a long/flat US equity overlay. See the transfer table.
- **"Improves risk-adjusted returns" vs "reduces drawdowns".** Several sources describe option-like protection (Glabadanidis's "protective put"; HOP's crisis performance) rather than a higher Sharpe ratio. The brief's question is about risk-adjusted return. Drawdown reduction on its own is not an answer to it and is not graded here.
- **Look-back choice is itself a trial.** Bajgrowicz & Scaillet [S9] bear directly on picking between 10-month, 200-day and other windows (see Follow-up questions).
- **Low statistical power** [S10] means that neither a positive nor a negative result on a single US index is decisive at any realistic sample length.
- **Citation discrepancy:** EconPapers lists the journal version of Bajgrowicz & Scaillet as "European Journal of Finance, Vol. 3", while the search results and ScienceDirect listing give JFE 106(3) 473–491. JFE is used here; the EconPapers entry looks like a metadata error (UNVERIFIED). The fetched abstract is the working-paper version (DJIA 1897–2008); the journal abstract, per the snippet, says 1897–2011.
- **Fetch attempts that returned nothing usable, not counted as sources (12):**
  - Unreadable PDFs (4): the Zakamulin 2014 PDF, the Zakamulin 2018 SSRN PDF, the MOP PDF (NYU Stern), and the HOP PDF (Yale course mirror). The open Clare et al. PDF was not attempted, for the same reason.
  - HTTP 403 (5): SSRN (two papers), Wiley (Zakamulin 2018), ResearchGate (Marshall), and Taylor & Francis (Marshall).
  - Other (3): Springer returned an authentication redirect (Zakamulin 2014), and the Semantic Scholar API returned publisher-elided abstracts for Glabadanidis and Marshall.

## UNVERIFIED items
- **Zakamulin 2014 [S1]:** index, sample period, out-of-sample split method, the transaction-cost assumption in bps, and the out-of-sample Sharpe ratios of timing vs buy-and-hold. Also whether outperformance is concentrated in bear markets.
- **Zakamulin 2018 [S2]:** the wording "only marginally better than … buy-and-hold" and "indistinguishable from the buy-and-hold strategy" (search snippet). Also the exact nature of the look-ahead, and whether it affects all of Glabadanidis's results.
- **Clare et al. 2013 [S3]:** whether any out-of-sample test was run, the cost assumption, and the size of the "dominance".
- **HOP [S4]:** "net of trading costs, a simulated 2% annual management fee and a 20% performance fee" (search snippet); net Sharpe; performance after 2012; the equity-index-only results.
- **MOP [S5]:** sample period; results for the equity-index subset; the cost discussion.
- **Huang et al. [S8]:** the asset universe and out-of-sample period.
- **Bajgrowicz & Scaillet [S9]:** whether 10-month-style MA rules sit in the rule universe; the journal-version sample end (2008 vs 2011).
- **Marshall et al. [S6]:** the definition of "large stock series" and any net-of-cost figures.

## Follow-up questions (not answered here)
1. Does a market-trend overlay reduce momentum-crash losses (Daniel & Moskowitz's "panic states", handoff H1) enough to change MOM's net Sharpe? This is a separate question about *conditioning momentum*, not timing the index.
2. If a trend overlay is pre-registered, how many look-back and rule variants count as trials in the registry? What deflation applies, given [S9]'s data-snooping result?
3. Is drawdown reduction without a Sharpe gain a valid objective under ADR 0005? That is an owner decision, not a research one.
4. A full-text pass (a tool that can parse PDFs, or manual reading) on S1, S2, S3 and S4 would close most of the UNVERIFIED items without new searches.
5. Does the post-2009 decay in short-term trend (Kurth et al. 2026, flag only) extend to 10-to-12-month trend on US equity indices?

## Sources
Retrieved 2026-09-25. Tier per handoff §6.1. All are Tier 1: peer-reviewed articles, cited via abstract or publication-record pages.

- **S1** Zakamulin, V. (2014). "The real-life performance of market timing with moving average and time-series momentum rules." *Journal of Asset Management* 15(4). Abstract via EconPapers: https://econpapers.repec.org/RePEc:pal:assmgt:v:15:y:2014:i:4:d:10.1057_jam.2014.25 (Tier 1)
- **S2** Zakamulin, V. (2018). "Revisiting the Profitability of Market Timing with Moving Averages." *International Review of Finance*. Abstract via the author's paper list: https://vzakamulin.weebly.com/papers1.html (Tier 1; the author's own page carrying the published abstract). DOI 10.1111/irfi.12132.
- **S3** Clare, A., Seaton, J., Smith, P. N., Thomas, S. (2013). "Breaking into the blackbox: Trend following, stop losses and the frequency of trading – the case of the S&P500." *Journal of Asset Management* 14(3) 182–194. City Research Online record: https://openaccess.city.ac.uk/17842/ (Tier 1)
- **S4** Hurst, B., Ooi, Y. H., Pedersen, L. H. (2017). "A Century of Evidence on Trend-Following Investing." *Journal of Portfolio Management* 44(1) 15–29. AQR article page: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing (Tier 1 article; the page is hosted by the authors' firm)
- **S5** Moskowitz, T. J., Ooi, Y. H., Pedersen, L. H. (2012). "Time Series Momentum." *Journal of Financial Economics* 104(2) 228–250. CBS Research Portal: https://research.cbs.dk/en/publications/time-series-momentum/ (Tier 1)
- **S6** Marshall, B. R., Nguyen, N. H., Visaltanachoti, N. (2017). "Time series momentum and moving average trading rules." *Quantitative Finance* 17(3) 405–421. IDEAS/RePEc: https://ideas.repec.org/a/taf/quantf/v17y2017i3p405-421.html (Tier 1)
- **S7** Glabadanidis, P. (2015). "Market Timing With Moving Averages." *International Review of Finance* 15(3) 387–425. IDEAS/RePEc: https://ideas.repec.org/a/bla/irvfin/v15y2015i3p387-425.html (Tier 1)
- **S8** Huang, D., Li, J., Wang, L., Zhou, G. (2020). "Time series momentum: Is it there?" *Journal of Financial Economics* 135(3) 774–794. WashU profile: https://profiles.wustl.edu/en/publications/time-series-momentum-is-it-there/ (Tier 1)
- **S9** Bajgrowicz, P., Scaillet, O. (2012). "Technical trading revisited: False discoveries, persistence tests, and transaction costs." *Journal of Financial Economics* 106(3) 473–491. Working-paper abstract via EconPapers: https://econpapers.repec.org/paper/chfrpseri/rp0805.htm (Tier 1)
- **S10** Zakamulin, V., Giner, J. (2022). "Time series momentum in the US stock market: Empirical evidence and theoretical analysis." *International Review of Financial Analysis* 82. IDEAS/RePEc: https://ideas.repec.org/a/eee/finana/v82y2022ics1057521922001363.html (Tier 1)

Search-snippet references (not fetched, flags only, UNVERIFIED):
- Zakamulin 2018: "only marginally better … indistinguishable".
- HOP: "2% annual management fee and a 20% performance fee".
- HOP JPM abstract: "8 out of 10 of the largest crisis periods".
- Bajgrowicz & Scaillet journal abstract: DJIA 1897–2011.
- Kurth, Eisler, Rej & Bouchaud (2026), arXiv 2607.01550: https://arxiv.org/html/2607.01550v1

## Budget note
Brief budget: 10 sources and 15 searches. **Used:** 10 of 10 sources (S1–S10) and 12 of 15 searches, plus 12 failed fetches that are not counted (listed under Caveats). The report is marked INCOMPLETE because the source budget ran out with the table-level figures unverified, not because the grade is missing.

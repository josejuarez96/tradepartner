# Research Report: does a published predictor's long-leg performance persist out of sample? A pre-declared test on all 212 Chen-Zimmermann predictors

**Brief:** #3756 (the owner's instruction of 2026-11-21; it runs the computation `2026-10-30-strategy-first-ranking.md` could not)  ·  **Date:** 2026-11-21  ·  **Status:** COMPLETE for the pre-declared test; the second measure below is post hoc and is marked so  ·  **Agent/model:** the orchestrating cloud session, with a shell. Open data only: Chen-Zimmermann release 2026.10 (Hugging Face `OpenSourceAP/CrossSection`) and the Ken French factor file. No store was opened, no trial was run, `.env` was not read.

## Answer
**Verdict:** NOT SUPPORTED: picking published predictors on their past long-leg performance does not give a long-side edge in non-micro stocks out of sample  ·  **Confidence:** medium-high on the pre-declared test; low on any single signal named below (each carries the whole search)

1. **The pre-declared test fails.** In the non-micro file, the 20 signals with the best long-leg alpha in 2005-2014 earned **-0.03 %/month (t -0.3)** against the market in 2015-2023. The pinned line was +0.20 %/month with t ≥ 2.0.
2. **Measured against its own universe, the average long leg earns about +0.05 %/month.** That is the same order as Chen and Welch's 7 bp (G2-S10), and below any trading cost we could pay. This measure is post hoc (see "Why a second measure").
3. **Rank persistence is real but lives on the losing side.** Across signals, alpha in the first decade correlates with alpha in the second (Spearman +0.33 to +0.49). The bottom 20 stay bad; the top 20 do not stay good. The short legs lose about 0.14 %/month against their universe in both decades. A long-only book cannot collect that.
4. **One cluster stands out, and it is not new to us: profitability and low net issuance.** Twelve signals have a long-leg t above 2 in 2015-2023 against their own universe, where chance gives about five. Nine of the twelve are profitability or issuance variants (CBOperProf, OperProfRD, RoE, MS, OPLeverage, ShareIss1Y, ShareIss5Y, ShareRepurchase, XFIN). They are annual, low-turnover signals worth roughly 0.2 to 0.5 %/month gross on the long leg over those nine years.
5. **Our store cannot confirm that cluster.** In the window our families practise on (2020-09 to 2023-12) the same long legs show +0.1 to +0.3 %/month with t near 0.5. Forty months is too short to tell 3 %/yr from zero.
6. **The benchmark did not cause the B3 and H1 kills.** I expected an equal-weighted non-micro book to lag the cap-weighted market enough to explain them. It does over 2015-2023 as a whole (-2.1 pp/yr), but in 2020-09 to 2023-12 the universe was +1.1 pp/yr ahead of the market. The 2026-11-20 audit's conclusion stands.

**One line:** the public anomaly zoo has no selectable long-side edge for us; what survives is a quality and low-issuance tilt of a few percent a year that looks like a factor premium, not a strategy, and that our history is too short to verify.

## What ran

| Item | Value |
|---|---|
| Method | Pinned in a file before any return was loaded (reproduced under "Method as pinned"). Stamp 2026-11-21T17:09Z |
| Files | `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct` (primary, non-micro), `PredictorAltPorts_DecilesVW` (secondary), `PredictorAltPorts_Deciles` and `PredictorAltPorts_LiqScreen_Price_gt_5` (context); `SignalDoc.csv`; `F-F_Research_Data_Factors` |
| Months | 2005-01 to 2023-12. Rows from 2024 on were dropped before any statistic (the dead months) |
| Windows | A = 2005-01 to 2014-12 (selection); B = 2015-01 to 2023-12 (test) |
| Long leg | A signal's highest-numbered portfolio (the dataset signs signals so high is long) |
| Signals | 212 predictors; 205 have at least 60 months in both windows in the primary file, 176 in the value-weighted file |

## Pre-declared results

CAPM alpha of the long leg, %/month (t in brackets).

| File | Spearman, alpha A vs B | Top 20 by t in A: A → B | Bottom 20: A → B | All signals: A → B | t > 2 in B (chance) |
|---|---:|---|---|---|---|
| Non-micro (primary) | +0.40 | +0.23 (3.5) → **-0.03 (-0.3)** | -0.42 (-2.2) → -0.43 (-1.1) | -0.06 (-0.4) → -0.29 (-1.2) | 2 of 205 (4.7) |
| Non-micro, published ≤ 2004 | +0.30 | +0.12 (1.0) → -0.31 (-1.6) | -0.32 (-1.8) → -0.51 (-1.7) | -0.09 (-0.6) → -0.36 (-1.5) | 0 of 82 (1.9) |
| Value-weighted deciles | +0.35 | +0.28 (4.9) → +0.13 (2.9) | -0.66 (-4.2) → -0.48 (-2.1) | -0.07 (-0.9) → -0.18 (-1.4) | 6 of 176 (4.0) |
| Value-weighted, published ≤ 2004 | +0.34 | +0.16 (2.5) → -0.18 (-1.7) | -0.50 (-2.9) → -0.54 (-2.1) | -0.13 (-1.3) → -0.29 (-1.8) | 1 of 64 (1.5) |
| Price > $5 (context) | +0.44 | +0.22 (3.7) → -0.02 (-0.2) | -0.42 (-2.5) → -0.38 (-1.1) | -0.06 (-0.5) → -0.25 (-1.1) | 3 of 208 (4.8) |
| All stocks, original weights (context) | +0.49 | +0.34 (2.3) → -0.09 (-0.4) | -0.52 (-2.1) → -0.78 (-1.8) | -0.06 (-0.3) → -0.40 (-1.3) | 2 of 176 (4.0) |

**Decision line (pinned):** top-20 in B at least +0.20 %/month with t ≥ 2.0 in the primary file. Result: -0.03 (t -0.3). **Fails.** The value-weighted file's +0.13 (t 2.9) is significant but below the line and is 1.5 %/yr before costs.

## Why a second measure (post hoc)

The pinned measure is alpha against the cap-weighted market. The average signal's long leg shows -0.29 %/month in B on it, which cannot be the signals: it is the cost of holding an equal-weighted non-micro book while mega-caps led. So the pinned measure mixes each signal's edge with its universe. The fix is the project's own rule (research program 4a (e): a matched benchmark): measure the long leg against the average of the same signal's ten portfolios. I did not think of this before the read, so everything in this section is post hoc.

The universe against the market: non-micro, A +1.0 pp/yr, B -2.1 pp/yr, 2020-09 to 2023-12 +1.1 pp/yr. Value-weighted deciles: -0.1, -1.2, +0.8.

Long leg minus its own universe, %/month (t):

| File | Spearman A vs B | Top 20 by t in A: A → B | Bottom 20: A → B | All signals: A → B | Short leg, all: A → B | t > 2 in B (chance) |
|---|---:|---|---|---|---|---|
| Non-micro | +0.33 | +0.28 (6.1) → +0.18 (2.0) | -0.22 (-4.6) → -0.13 (-1.5) | +0.03 (1.5) → +0.05 (1.3) | -0.14 (-2.8) → -0.14 (-1.7) | 12 of 205 (4.7) |
| Value-weighted | +0.18 | +0.41 (5.6) → +0.09 (0.7) | -0.40 (-3.1) → -0.22 (-1.6) | +0.04 (0.8) → +0.02 (0.3) | -0.16 (-1.9) → -0.15 (-1.3) | 10 of 176 (4.0) |

On this measure the top-20 portfolio earns +0.18 %/month in B in the non-micro file: about 2 %/yr gross, just under the pinned line, borderline in significance, and found after the fact. It is the most favourable reading available and it is still not a strategy at our costs.

The twelve signals with t > 2 in B (non-micro, long minus universe): CBOperProf +0.55, OperProfRD +0.52, RIO_Volatility +0.51, RDIPO +0.43, MS +0.37, OPLeverage +0.26, ShareIss1Y +0.25, XFIN +0.23, ShareIss5Y +0.23, RoE +0.21, ShareRepurchase +0.17, VolumeTrend +0.13. Only two of them (RIO_Volatility, MS) also had t > 2 in A.

Ideas this project tested or killed, on the same measure (A, B, and 2020-09 to 2023-12):

| Signal | A | B | 2020-09 to 2023-12 |
|---|---|---|---|
| Mom12m (H1's signal) | -0.30 (-1.1) | +0.26 (0.8) | +0.19 (0.3) |
| GP (B3's signal) | +0.03 (0.2) | +0.35 (2.0) | +0.08 (0.2) |
| CBOperProf | +0.11 (0.7) | +0.55 (2.4) | +0.25 (0.5) |
| AssetGrowth (I1) | +0.03 (0.3) | +0.13 (0.6) | +0.39 (0.7) |
| ShareIss1Y | +0.07 (0.7) | +0.25 (2.1) | +0.73 (2.9) |
| XFIN | +0.11 (1.3) | +0.23 (2.0) | +0.32 (1.5) |
| AnnouncementReturn (B9, E1) | +0.20 (2.2) | -0.02 (-0.2) | -0.16 (-0.8) |
| STreversal (IRRX's base) | -0.35 (-1.2) | -0.02 (-0.1) | -0.72 (-1.0) |
| cfp | +0.23 (1.8) | +0.31 (1.2) | +1.50 (3.6) |

Two things follow. The earnings-announcement and reversal lines (B9, E1, IRRX) show nothing on the long side in non-micro stocks after 2015, before costs, which agrees with their kills and with stopping IRRX. The profitability and issuance kills were made at desk reads whose rule asked for both legs or for 2010-2019 thresholds; on a long-leg-against-universe reading they are the only cluster with a positive record, at t near 2 over nine years.

## Caveats
- **Search.** Every signal's test-window figure was seen. Any signal named above carries all 212 looks, and the cluster in finding 4 was noticed after the read. Its members are close variants of each other, so "12 against 5 by chance" overstates the independent evidence.
- **Gross.** No cost is in any figure. Annual signals turn over slowly, so cost is small for the cluster in finding 4 and decisive for anything monthly.
- **Not our universe.** The non-micro file screens at the NYSE 20th size percentile with the papers' own weighting. Our top-1,000 universe with a dollar-volume floor is narrower.
- **The post hoc measure** was chosen after seeing that the pinned one was contaminated. It follows a rule the project already has, but it was not pinned.
- **TP-10** retired published slow accounting signals as a source. Nothing here un-retires them; a return is a new registration, and the owner's word.

## What this means for the search
- The published zoo is not a place to find a strategy by ranking. That question is now answered on data rather than on summaries.
- The only public-data lead with the passable shape for a **trial** (low turnover, positive net) is a profitability and low-issuance long tilt. Its honest description is a factor tilt of a few percent a year, comparable to holding a quality fund, with a nine-year t near 2 and no way to confirm it on forty months of our own history.
- Whether that is worth a paper book is a question about the objective (PR #3698), not about measurement.

## Method as pinned (verbatim, before any return was read)

Question: in the published-predictor zoo, for stocks we could trade (non-micro), is there anything to find? Not "which signal looks best" (212 looks pick noise) but "does past performance of a signal's long leg persist out of sample".

Months read: 2005-01 to 2023-12 only. Windows: A = 2005-01..2014-12 (selection), B = 2015-01..2023-12 (test). Long leg of a signal in a file = its highest-numbered portfolio. Measure: CAPM alpha of the long leg, monthly: regress (long - RF) on (Mkt-RF) within the window.

Pre-declared statistics (primary file, repeated on secondary): 1. Spearman rank correlation across signals of alpha in A with alpha in B. 2. Take the 20 signals with the highest alpha t in A; form their equal-weight long-leg portfolio in B; report its CAPM alpha and t in B. Same for the bottom 20, and for all signals. 3. Count of signals with alpha t > 2 in B, against the count expected by chance. 4. Mean alpha across signals per file per window. 5. Restricted repeat on signals published 2004 or earlier.

Decision line, pinned: the zoo is "worth an own-store read of a composite" only if statistic 2's top-20 portfolio in B has alpha >= 0.20 %/month with t >= 2.0 in the PRIMARY file. Otherwise the answer is "nothing to find here that selection can reach".

Looks: this is one pre-declared procedure over all signals; it adds one ledger row, not 212, but every signal's B-window figure is seen, so any single signal picked from the output afterwards carries the full search.

## Reproduce
Two scripts, kept in the session scratchpad and summarised here: `cz_persist.py` (the pinned test: load a file, keep 2005-01 to 2023-12, take each signal's top portfolio, regress its excess return on Mkt-RF per window, then the five statistics) and `cz_relative.py` (the post hoc measure: the same, with each long leg less the mean of that signal's portfolios, and a plain t on the difference). Both read only `signalname`, `port`, `date`, `ret`.

## Sources
- Chen, A. Y., and Zimmermann, T., Open Source Asset Pricing, release 2026.10, Hugging Face dataset `OpenSourceAP/CrossSection`, files named above. Accessed 2026-11-21. T1 (replication dataset).
- Kenneth R. French data library, `F-F_Research_Data_Factors` (monthly Mkt-RF and RF), built from the 202609 CRSP database. Accessed 2026-11-21. T1.

## Proposed claims (not added to `claims.toml` here)

| id | claim | grade |
|---|---|---|
| ZP-1 | In CZ non-micro portfolios, the 20 predictors with the best long-leg CAPM alpha t in 2005-2014 earned -0.03 %/month (t -0.3) in 2015-2023; a pre-declared +0.20 line failed. | SUPPORTED (our computation) |
| ZP-2 | The average predictor's long leg earned about +0.05 %/month against its own universe in 2015-2023 (non-micro); short legs lost about 0.14. | SUPPORTED (post hoc measure) |
| ZP-3 | Profitability and net-issuance predictors are over-represented among long legs with t > 2 in 2015-2023 (9 of 12), at 0.2 to 0.5 %/month gross; found after the read. | INSUFFICIENT (post hoc, correlated variants) |
| ZP-4 | An equal-weighted non-micro universe led the cap-weighted market by about 1.1 pp/yr in 2020-09 to 2023-12, so the benchmark does not explain B3's or H1's kill. | SUPPORTED (our computation) |

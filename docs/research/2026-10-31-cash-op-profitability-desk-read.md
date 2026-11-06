# Research Report: cash-based operating profitability desk read

**Brief:** #2675, candidate 4  ·  **Date:** 2026-10-31  ·  **Status:** COMPLETE  ·  **Agent/model:** team codexcbop, Codex; local DuckDB/Pandas read only

## Answer

**Verdict: FAIL** under the [backlog rule self-reported as frozen at 2026-10-31 01:02:44 EDT](hypothesis-backlog.md#cboperprof-cash-based-operating-profitability-candidate-4-desk-read-2675), before any `CBOperProf`/`OperProfRD` return was read. All six `CBOperProf` file × window cells are comparable and all six fail: the short leg is below its positive floor in each. The 2020+ L/S t is also below 1.0 in both files. The gross spreads clear the base trading-and-borrow cost line, but the frozen rule requires the legs and t as well. This retires the `CBOperProf` candidate. **T330 and T331 must not be built for this failed signal:** their gate requires a PASS, and the plan's written-entry gate stub alone does not establish one. The owner can [strike or park those plan tasks on #2677](https://github.com/josejuarez96/tradepartner/issues/2677). `OperProfRD` is a labelled check, not a rescue or independent verdict. A pass would have unlocked T330 then T331 and, for a later frozen finalist, the [ADR 0016 point-8 historical exam](../decisions/0016-development-boundary-and-forward-exams.md), which can only reject on its partially seen post-boundary months.

## Evidence

The read is reproducible from [compute.py](/Users/josejuarez/tradepartner-probes/cbop/compute.py) and its [out.txt](/Users/josejuarez/tradepartner-probes/cbop/out.txt). It reads the two local Chen–Zimmermann parquets named in the backlog; `ret` is percent per month. Both have `01`–`10` and `LS` for both signals, with complete monthly dates through **2025-12**. In every declared cell, `LS = 10 − 01` to floating-point precision (maximum absolute error below 8×10⁻¹⁴ percentage points). High `10` is therefore the supplied favourable side. Long = `10` minus the equal mean of ten portfolios; short = that mean minus `01`. t is mean divided by the ordinary sample standard error (no HAC or selection correction). Positive share counts months with L/S > 0. Worst 12m is the lowest rolling 12-month compounded L/S return, a one-year loss measure rather than a peak-to-trough maximum drawdown.

| CBOperProf file | Window (months) | L/S %/mo (t) | Long %/mo (t) | Short %/mo (t) | L/S positive | Worst 12m |
|---|---|---:|---:|---:|---:|---:|
| VW | 2009+ (204) | +0.463 (1.37) | +0.410 (2.51) | +0.053 (0.21) | 110/204 (53.9%) | −29.23% |
| VW | 2016+ (120) | +0.523 (1.06) | +0.558 (2.31) | −0.035 (−0.10) | 71/120 (59.2%) | −29.23% |
| VW | 2020+ (72) | +0.360 (0.49) | +0.640 (1.76) | −0.280 (−0.52) | 42/72 (58.3%) | −29.23% |
| NYSE-p20 screen | 2009+ (204) | +0.450 (1.42) | +0.434 (2.67) | +0.016 (0.07) | 107/204 (52.5%) | −23.48% |
| NYSE-p20 screen | 2016+ (120) | +0.509 (1.12) | +0.583 (2.41) | −0.074 (−0.22) | 68/120 (56.7%) | −23.48% |
| NYSE-p20 screen | 2020+ (72) | +0.376 (0.56) | +0.666 (1.84) | −0.290 (−0.61) | 39/72 (54.2%) | −23.48% |

**Literal rule application.** VW uses the declared +15.6 bp/side liquid proxy: L/S floor **0.1540%/month**, long floor **0.1000**, short floor **0.1020**, and every t must be ≥1.0. The screen uses +24.2 bp/side: L/S floor **0.2113**, long floor **0.1000**, short floor **0.1307**, and every t must be ≥1.0. Each row clears the L/S and long-mean floors; every short-mean and short-t fails. The VW and screened 2020+ L/S t also fails. No file was selected after its returns were seen. At the pre-declared **2× turnover stress** (200%/year per leg), L/S floors rise to **0.2580** VW and **0.3727** screened. All VW spreads clear the stress spread floor; the screen 2020+ spread narrowly clears it (+0.3762). Leg and t failures remain, so the stress verdict is also FAIL. These costs assume 30 bp/year short borrow and a 2× margin; actual turnover is unknown.

**OperProfRD, labelled check only.** Same six cuts were computed, without entering the `CBOperProf` decision:

| File | Window | L/S %/mo (t) | Long %/mo (t) | Short %/mo (t) | Positive | Worst 12m |
|---|---|---:|---:|---:|---:|---:|
| VW | 2009+ | +0.551 (1.58) | +0.365 (2.17) | +0.186 (0.75) | 109/204 (53.4%) | −30.76% |
| VW | 2016+ | +0.698 (1.37) | +0.578 (2.37) | +0.120 (0.32) | 65/120 (54.2%) | −30.76% |
| VW | 2020+ | +0.412 (0.56) | +0.634 (1.74) | −0.222 (−0.42) | 35/72 (48.6%) | −30.76% |
| NYSE-p20 screen | 2009+ | +0.432 (1.38) | +0.358 (2.17) | +0.073 (0.35) | 110/204 (53.9%) | −28.46% |
| NYSE-p20 screen | 2016+ | +0.745 (1.64) | +0.589 (2.48) | +0.156 (0.50) | 68/120 (56.7%) | −21.63% |
| NYSE-p20 screen | 2020+ | +0.602 (0.92) | +0.669 (1.89) | −0.067 (−0.15) | 39/72 (54.2%) | −21.63% |

## Disconfirmation

- The positive `CBOperProf` L/S and long legs are real counterevidence to a blanket claim that profitability has no gross return. They do not establish an implementable net short book: the short leg is weak or negative versus the equal portfolio mean and its t is far below 1.0.
- Chen–Welch's luck adjustment puts its best non-microcap raw post-2005 anomaly near **0.06%/month** after selection adjustment, below even this candidate's base cost line. The [strategy-first ranking](2026-10-30-strategy-first-ranking.md) records this as **Tier 2** evidence; it is a prior, not a correction applied to these six observed means.
- The reported 2009+ window overlaps Ball et al.'s sample through 2014, so it is not wholly post-publication. The 2016+ and 2020+ cuts check decay, and their L/S t falls sharply by 2020+ even while the long mean rises. Overlapping windows are descriptive, not independent replications.
- Profitability may overlap broad quality and market beta. The [B3 gross-profitability result](2026-10-18-b9-kill-b3-retirement.md) was a **long-only, index-like** book that lost versus SPY; it did not test this cash-based L/S formula. The CZ long-versus-equal-decile statistic does not remove beta or quality exposure. Whether that explains the long result remains untested.

## Caveats

- The 24.2 and 15.6 bp/side inputs are the requested provisional [paper-fill study](2026-10-30-paper-fill-cost-arrival.md) proxies. The [ADR 0023 amendment](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-10-30-2667-one-rung-1-cost-rule) actually specifies rebalance days, arrival midpoint, equal buy/sell weighting and the book's own liquidity band. The study reports +17.0 bp all-name rebalance days and +10.6 bp over-$200M all orders; +15.6 is its liquid buy mean. The frozen proxy is conservative, but it is not a measured cost for this proposed book.
- The NYSE market-equity p20 screen is not TradePartner's $200M median dollar-volume floor; VW is not a literal over-$200M portfolio. CZ gross returns include neither TradePartner execution nor borrow availability, taxes, margin financing, or delister/point-in-time coverage checks. The 100% annual one-sided turnover estimate follows the catalogue's annual portfolio period, not observed constituent turnover.
- The [strategy-first ranking](2026-10-30-strategy-first-ranking.md) actually estimates 10% one-sided turnover per month per leg (about 120% per year), so the 100% frozen base cost floors are light; the 200% stress brackets that estimate. The frozen kill line remains as written, and the short-leg failures hold even before costs.
- The owner approved [Ball et al. signs, lagged total assets, and separately labelled broad working-capital proxies](../specs/data-foundation.md#amendment-2026-10-30-2670-delisting-outcomes-and-cash-based-operating-profitability). This desk read uses CZ's supplied signal; it does not validate a XBRL implementation or establish that Ball et al. themselves used lagged assets. No missing accounting component may be silently set to zero in a later derivation.
- The 2020+ CZ read exposes months inside ADR 0016 point 8's fixed historical exam interval. Any later exam of this family would have to disclose that exposure and can only reject a frozen finalist; this desk read spends no registered store holdout.

## Exploratory-cut ledger

**No extra return cuts.** The 12 file × signal × window cells above were named before any `CBOperProf`/`OperProfRD` return was read: six `CBOperProf` decision cells and six `OperProfRD` labelled checks. The 01:03:41 EDT first run completed and printed returns for all 12 cells, including both legs, t statistics, positive shares and worst 12-month returns; its `out.txt` was overwritten by the 01:05:41 rerun. After seeing that first output, the 01:05:37 edit split and reformatted printed lines, assigned intermediate values for display, and computed each displayed mean/t pair once instead of twice. It did not change the file or signal selection, windows, portfolio or leg formulas, thresholds, or returned values. No alternate file, weighting, start date, threshold or signal was selected after reading numbers. The 2× turnover row was frozen as a stress sensitivity, not a second promotion rule. The 01:02:44 freeze time is self-reported: its first durable repository record was the 01:06:46 commit, after the runs. Future reads should post the kill line on the issue before computing. These looks count as disclosed research exposure, and the candidate enters the backlog search-limit tally because the rung-2 read occurred.

## UNVERIFIED

- Exact CZ constituent membership, actual annual one-sided turnover, borrow locates/cost, and applicability of the paper-fill cost to a new monthly book.
- Equality of the CZ cash-based operating-profitability construction and a future first-vintage, point-in-time XBRL implementation, including tag coverage and period alignment. Ball et al.'s cited denominator page was not available locally.
- Whether quality or beta adjustment removes the long-leg return; ordinary t has no autocorrelation or multiple-search adjustment.
- Candidate 3 is the B7 Form 4 insider-purchase read ([#2673](https://github.com/josejuarez96/tradepartner/issues/2673), [PR #2674](https://github.com/josejuarez96/tradepartner/pull/2674), INSUFFICIENT); this `CBOperProf` read is candidate 4. The backlog tally must be reconciled to 4 of 6 when both PRs land.

## Sources

- Chen–Zimmermann local `PredictorAltPorts_DecilesVW.parquet` and `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet`, read by the script linked above; no network or store read.
- [Frozen backlog declaration and strategy-search tally](hypothesis-backlog.md), [strategy-first ranking](2026-10-30-strategy-first-ranking.md), [paper-fill cost study](2026-10-30-paper-fill-cost-arrival.md), [ADR 0023 cost rule](../decisions/0023-break-first-fast-books-paper-exam.md), [ADR 0016 point 8](../decisions/0016-development-boundary-and-forward-exams.md), [data-foundation amendment](../specs/data-foundation.md), and [B3 retirement](2026-10-18-b9-kill-b3-retirement.md).

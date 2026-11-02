# Research Report: net external financing (XFIN) desk read

**Brief:** #2828, candidate 6 of 6  ·  **Date:** 2026-11-01  ·  **Status:** COMPLETE  ·  **Agent/model:** team opusxfin, claude-opus-5-5; local DuckDB/Pandas read of two Chen–Zimmermann parquets only, no store read

## Answer

**Verdict: FAIL** under the [kill line frozen on #2828](https://github.com/josejuarez96/tradepartner/issues/2828#issuecomment-6567898743), posted 2026-11-02 02:52:38 UTC before any `XFIN` return was read. The script's single run started at 02:53:07 UTC. All six `XFIN` file × window cells are comparable, and all six fail:

- In the value-weighted file, every cell passes the L/S, long and short mean floors, but the short leg's t is below 1.0 (0.58 to 0.77).
- In the size-screened file, the long leg is below its +0.10%/month floor and its t is below 1.0 in every window. From 2016 on, the L/S and short t also fall below 1.0.

The rule is "FAIL if any comparable cell fails", so this retires `XFIN` at rung 2. No store test is proposed. The declared secondary, `NetEquityFinance`, fails all six cells as well. It is descriptive only and had already been read on 2026-10-30.

This is the sixth candidate, so the [strategy-search limit](hypothesis-backlog.md) is reached. The rule is "6 candidates or 2026-11-27, whichever comes first", so the owner's retro (continue, change course or stop) is now due.

## Evidence

The read can be reproduced from [compute.py](/Users/josejuarez/tradepartner-probes/xfin/compute.py) and its [out.txt](/Users/josejuarez/tradepartner-probes/xfin/out.txt), which was written once and never overwritten.

**Inputs.**
- The script checks each parquet's SHA-256 against the frozen pins before reading it.
  - VW `95409cb1…79a8`.
  - Screen `fcd2ae40…3734`.
  - Both equal the Hugging Face LFS object ids at `OpenSourceAP/CrossSection` commit `ad1dfba5`.
- `ret` is percent per month.
- Both files hold ports `01` to `10` and `LS` for every month through 2025-12.

**Constructions.**
- In every cell, `LS = 10 − 01` to within 8×10⁻¹⁴ percentage points. By the frozen rule, high `10` is the favourable side, meaning the firms paying out the most net financing (CZ orders the portfolios that way).
- Long = `10` minus the equal mean of the ten portfolios.
- Short = that mean minus `01`.
- t is the mean divided by the ordinary standard error, with no HAC or selection correction.
- Worst 12m is the lowest rolling 12-month compounded L/S return. It measures a one-year loss, not a peak-to-trough drawdown.

**Floors** (base case: 120% one-sided turnover per leg per year, 30 bp/year borrow, 2× margin).

| File | Cost per side | L/S | Long | Short | t floor |
|---|---:|---:|---:|---:|---:|
| VW | +15.6 bp | 0.1748 | 0.1000 | 0.1124 | 1.0 |
| Screen | +24.2 bp | 0.2436 | 0.1000 | 0.1468 | 1.0 |

| XFIN file | Window (months) | L/S %/mo (t) | Long %/mo (t) | Short %/mo (t) | L/S positive | Worst 12m | Cell |
|---|---|---:|---:|---:|---:|---:|---|
| VW | 2009+ (204) | +0.398 (1.03) | +0.225 (1.63) | +0.173 (**0.58**) | 107/204 (52.5%) | −36.28% | FAIL: short t |
| VW | 2016+ (120) | +0.632 (1.05) | +0.330 (1.64) | +0.302 (**0.64**) | 63/120 (52.5%) | −36.28% | FAIL: short t |
| VW | 2020+ (72) | +0.973 (1.10) | +0.439 (1.42) | +0.534 (**0.77**) | 42/72 (58.3%) | −36.28% | FAIL: short t |
| NYSE-p20 screen | 2009+ (204) | +0.358 (1.18) | **+0.079 (0.99)** | +0.279 (1.09) | 113/204 (55.4%) | −29.90% | FAIL: long mean, long t |
| NYSE-p20 screen | 2016+ (120) | +0.411 (**0.87**) | **+0.092 (0.80)** | +0.320 (**0.80**) | 65/120 (54.2%) | −29.90% | FAIL: L/S t, long, short t |
| NYSE-p20 screen | 2020+ (72) | +0.564 (**0.83**) | **+0.087 (0.54)** | +0.477 (**0.82**) | 40/72 (55.6%) | −27.06% | FAIL: L/S t, long, short t |

**Literal rule application.** Bold marks each requirement a cell misses. The script compared unrounded values with the exact floors. The screened 2009+ long-leg t (0.993) is a near miss, but that cell also fails on its long mean (+0.0786 < 0.10), so the verdict does not depend on rounding. No file, window or side was chosen after the returns were seen.

**2× turnover stress (200% per year).** The L/S floors rise to **0.2580** VW and **0.3727** screened, and the leg floors are unchanged. All six cells still fail; the screen also fails its 2009+ L/S mean (0.358 < 0.373). The stress is reported only and does not change the base verdict.

**NetEquityFinance, declared secondary only.** The same script ran the same six cuts. Its means equal those of the [2026-10-30 issuance desk read](2026-10-30-equity-issuance-desk-read.md) to three decimals and its t values to within rounding, which cross-checks the script. They were seen before this freeze, so they are not evidence for or against `XFIN`.

| File | Window | L/S %/mo (t) | Long %/mo (t) | Short %/mo (t) | Cell |
|---|---|---:|---:|---:|---|
| VW | 2009+ | +0.147 (0.42) | +0.156 (1.06) | −0.009 (−0.03) | FAIL |
| VW | 2016+ | +0.204 (0.38) | +0.282 (1.41) | −0.078 (−0.19) | FAIL |
| VW | 2020+ | −0.012 (−0.01) | +0.222 (0.75) | −0.234 (−0.36) | FAIL |
| NYSE-p20 screen | 2009+ | +0.315 (1.21) | +0.083 (0.82) | +0.231 (1.10) | FAIL |
| NYSE-p20 screen | 2016+ | +0.256 (0.65) | +0.085 (0.61) | +0.171 (0.52) | FAIL |
| NYSE-p20 screen | 2020+ | +0.272 (0.47) | +0.058 (0.30) | +0.214 (0.44) | FAIL |

## Disconfirmation

**Against retiring the idea:**
- `XFIN` has more gross return than any earlier candidate. Its VW L/S is positive with t just above 1.0 in all three windows, and it rises from 2009+ to 2020+. Its VW long leg has t 1.4 to 1.6, and every VW mean clears its cost floor.
- Adding debt financing to `NetEquityFinance` raises the VW short leg from roughly zero or negative to +0.17 to +0.53. That fits Bradshaw–Richardson–Sloan's point that total financing, not equity alone, carries the signal.
- These are real gross effects. The read does not show that net external financing is priced at zero.

**For the verdict:**
- In the VW file, which holds the names TradePartner could trade, the short leg's t is 0.58 to 0.77, so the half that carries borrow is noise. The 2016+ and 2020+ L/S t of 1.05 and 1.10 come from 120 and 72 overlapping months, not independent samples.
- In the size-screened file, the long leg is small (+0.08 to +0.09) and well inside noise. The return there sits on the short side, which is the costliest leg to hold.
- The legs disagree across files: long in VW, short in screened. Chen–Welch's luck-adjusted best non-microcap post-2005 edge is about **0.06%/month** ([ranking](2026-10-30-strategy-first-ranking.md), a Tier 2 prior), below every floor here.
- QI-15 records that investment, issuance and financing overlap. `AssetGrowth` and the issuance signals already failed their reads ([I1](2026-10-30-i1-asset-growth-desk-read.md), [issuance](2026-10-30-equity-issuance-desk-read.md)), and `XFIN` falls in the same family.

## Caveats

**Costs.**
- The +24.2 and +15.6 bp/side inputs are the provisional [paper-fill study](2026-10-30-paper-fill-cost-arrival.md) proxies, the same ones #2675 used. They are not the cost that the [ADR 0023 amendment](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-10-30-2667-one-rung-1-cost-rule) defines for a proposed monthly book. +15.6 is a buy mean; the liquid per-side figure is 10.6.
- The 120% annual turnover follows the ranking's 10%/month estimate. Actual CZ turnover is unknown. Neither leg of the verdict depends on it: the VW short-t failures and the screened long-leg failures hold before costs.

**Coverage.**
- The NYSE market-equity p20 screen is not TradePartner's $200M median dollar-volume floor, and the VW file is not a literal over-$200M portfolio.
- CZ gross returns leave out TradePartner execution, borrow availability, taxes and financing.
- `NetDebtFinance` is in both files and was **not** read.

**Windows.**
- The 2009+ start is wholly post-publication for `XFIN`, whose sample ends in 2000 and which was published in 2006.
- The read does not use the backlog's L4 window ending 2019-12; it follows the `CBOperProf` precedent. The 2020+ window exposes months inside the [ADR 0016 point 8](../decisions/0016-development-boundary-and-forward-exams.md) exam interval. No registered store holdout is spent.

**Definition.** The CZ `XFIN` construction is taken from CZ's catalogue (not read here). The [data-foundation spec](../specs/data-foundation.md) records the published definition: (stock sales − repurchases − total dividends + debt issued − debt repaid, long- and short-term) / average total assets. Whether CZ's file matches that exactly is UNVERIFIED.

## What a survivor's store test would have needed

None is proposed, because `XFIN` failed. This section is recorded for a later re-entry. The [2026-11-01 data-foundation amendment](../specs/data-foundation.md) (#2826, PR #2827) adds the inputs as first-vintage, acceptance-timed statement names:

- `stock_issued`, `buybacks`, `dividends_paid`, `debt_issued` and `debt_repaid`.
- `total_assets`, averaged over t and t−1.

A test would also need:

- **Missing values stay missing.** A missing component (`stock_issued` is present in only 37 to 46% of active CIKs a year, per #2825) is never set to zero, and the excluded share is reported.
- **Dividend scope.** `dividends_paid` maps to common dividends only, while the published definition uses total dividends. That gap must be labelled.
- **Debt scope.** `debt_issued` and `debt_repaid` take the total-debt concepts. The long-term-only lines are a separate, narrower name.
- **Universe and liquidity.** The universe comes from `universe_as_of(t)`, with the $200M liquidity floor from bars known at t.
- **Delisters.** Delister coverage is adequate only from about 2020, so the test must start after 2020-08-31.
- **Short leg.** Borrow availability must be checked before a short leg is assumed.
- **Registration.** A new frozen registration, disclosing this desk read.

## Exploratory-cut ledger

**No extra return cuts.** The 12 cuts above (six `XFIN` decision cells and six `NetEquityFinance` secondary cells) were all named in the frozen comment.

Before the freeze, the agent ran one structure probe, `~/tradepartner-probes/xfin/structure.py`. It selected port labels, month counts and date spans only, with no `ret` column. Its findings are stated in the freeze.

`compute.py` was written after the freeze and run once (02:53:07 UTC). There was no rerun, no edit after the run, and no alternate file, weighting, window, side or threshold.

This read is candidate 6 in the search-limit tally.

## UNVERIFIED

- Exact CZ `XFIN` construction (dividend and debt scope, scaling denominator), constituent membership and turnover.
- Borrow locates and cost for the high-financing decile.
- Whether the VW long leg survives a beta or quality adjustment.
- Applicability of the paper-fill cost proxy to a monthly book.

## Sources

- Chen–Zimmermann `PredictorAltPorts_DecilesVW.parquet` and `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet`, pinned by SHA-256 on #2828 and read by the script linked above.
- Repository records:
  - [Frozen kill line on #2828](https://github.com/josejuarez96/tradepartner/issues/2828#issuecomment-6567898743)
  - [Hypothesis backlog](hypothesis-backlog.md)
  - [Strategy-first ranking](2026-10-30-strategy-first-ranking.md), rows 3 and 4
  - [Cash-based operating profitability desk read](2026-10-31-cash-op-profitability-desk-read.md), whose structure this read copies
  - [Issuance desk read](2026-10-30-equity-issuance-desk-read.md)
  - [Paper-fill cost study](2026-10-30-paper-fill-cost-arrival.md)
  - [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) and [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md)
  - [Data-foundation spec](../specs/data-foundation.md)

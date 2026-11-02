# Research Report: composite of weak slow signals, desk read

**Brief:** #2888, candidate 2 of 4 under the second strategy-search limit  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE  ·  **Agent/model:** team rescomposite, claude-opus-5-5. It reads two Chen–Zimmermann parquets and the CZ `SignalDoc.csv` locally with DuckDB and Pandas, and reads nothing from the store.

## Answer

**Verdict: FAIL** (evidence grade for "an equal-weight composite of low-correlated slow accounting signals clears 0.10 %/month at t ≥ 1 in every post-2009 cell": **MIXED**, confidence medium: five of six cells clear the floors, but by the [handoff §6.1](2026-09-24-initial-research-handoff.md) definition costs consume the effect, since the composite sits below the #2828 cost floors in four of six cells).

The rule is the [kill line frozen on #2888](https://github.com/josejuarez96/tradepartner/issues/2888#issuecomment-6583038848). It was posted at 2026-11-02 16:57:35 UTC, before any return was read. The script's single run started at 16:58:28 UTC.

- The script picked eight signals on 1990–2008 data only.
- Their equal-weight long-short composite passes five of the six file × window cells.
- The screened 2016+ cell has t **0.993**, below the 1.0 floor.
- The rule is "PASS only if all six pass", so the composite is retired at rung 2.

The composite is a near miss on the frozen floors, but every screened cell sits below the cost floors from #2828. A pass would not have shown net alpha.

This read is **candidate 2 of 4** under the [second strategy-search limit](hypothesis-backlog.md).

## Evidence

The read can be reproduced from [compute.py](/Users/josejuarez/tradepartner-probes/composite/compute.py) and its [out.txt](/Users/josejuarez/tradepartner-probes/composite/out.txt). `out.txt` was written once and set read-only.

**Inputs.**
- The script checks every input against its frozen SHA-256 before reading it:
  - the VW parquet, `95409cb1…79a8`;
  - the screen parquet, `fcd2ae40…3734`;
  - `SignalDoc.csv`, `0ceb1102…0178`.
- The two parquets are the same files as #2828.
- `ret` is in percent per month. Only the `LS` port enters the decision.

**Pool.** CZ's catalogue has no "slow" category. Its `Portfolio Period` column is the original paper's holding period, so it cannot tell slow signals from fast ones. The freeze used a substitute rule instead:

- every `Predictor` with `Cat.Data = Accounting` and `SampleEndYear ≤ 2005` (79 signals);
- plus the first limit's desk-read signals (`CBOperProf` is the only one the rule does not already include).

That makes 80 signals. 73 of them have `LS` in all 432 months from 1990-01 to 2025-12 in both files. The other 7 have no VW series.

**Selection (VW `LS`, 1990-01 to 2008-12, 228 months).** Signals are ranked by descending t. A signal joins only if its |corr| with every signal already selected is at most 0.30. Selection stops at 8.

| Order | Signal | Pre-2009 mean %/mo | Pre-2009 t |
|---:|---|---:|---:|
| 1 | `ShareIss1Y` | +0.800 | 3.78 |
| 2 | `NOA` | +0.833 | 3.40 |
| 3 | `BM` | +0.817 | 2.72 |
| 4 | `Tax` | +0.535 | 2.52 |
| 5 | `PS` | +0.990 | 1.92 |
| 6 | `DelEqu` | +0.604 | 1.66 |
| 7 | `NetDebtFinance` | +0.329 | 1.63 |
| 8 | `realestate` | +0.456 | 1.47 |

- **Skipped on correlation:** `EquityDuration`, `CBOperProf`, `dNoa`, `ChInvIA`, `AssetGrowth`, `RDS`, `XFIN`, `ShareIss5Y` and others ranked between these eight.
- **Correlation among the eight:** in the selection window the largest |corr| between any two is 0.299 (`ShareIss1Y` with `PS`). In 2009+ the mean pairwise corr (VW) is +0.018.

**Composite.** The composite is the equal-weight mean of the eight `LS` series each month. Its t is the mean divided by the ordinary standard error, with no HAC and no selection correction (as in #2828).

| File | Window (months) | Composite %/mo | t | L/S positive | Worst 12m | #2828 cost floor (reported only) | Cell |
|---|---|---:|---:|---:|---:|---:|---|
| VW | 2009+ (204) | +0.210 | 1.81 | 107/204 (52.5%) | −8.81% | 0.1748, above | PASS |
| VW | 2016+ (120) | +0.166 | 1.07 | 63/120 (52.5%) | −8.81% | 0.1748, below | PASS |
| VW | 2020+ (72) | +0.340 | 1.58 | 40/72 (55.6%) | −6.94% | 0.1748, above | PASS |
| NYSE-p20 screen | 2009+ (204) | +0.143 | 1.56 | 122/204 (59.8%) | −8.63% | 0.2436, below | PASS |
| NYSE-p20 screen | 2016+ (120) | +0.118 | **0.993** | 70/120 (58.3%) | −4.94% | 0.2436, below | **FAIL: t < 1** |
| NYSE-p20 screen | 2020+ (72) | +0.192 | 1.18 | 45/72 (62.5%) | −4.54% | 0.2436, below | PASS |

**Literal rule application.**
- The script compared unrounded values against the exact floors (0.10 %/month and t 1.0).
- The failing cell's t is 0.9934. The rule says a strict shortfall fails, so no rounding changes the verdict.
- No file, window, signal or threshold was chosen after the returns were seen.

**Per-signal means (descriptive only; they can neither rescue nor kill).** Each cell below is mean %/month, with t in brackets.

| Signal | VW 2009+ | VW 2016+ | VW 2020+ | Screen 2009+ | Screen 2016+ | Screen 2020+ |
|---|---:|---:|---:|---:|---:|---:|
| `ShareIss1Y` | +0.671 (2.48) | +0.799 (1.96) | +0.662 (1.02) | +0.350 (1.68) | +0.380 (1.20) | +0.559 (1.20) |
| `NOA` | +0.421 (1.61) | +0.498 (1.38) | +0.434 (0.90) | +0.188 (0.84) | +0.260 (0.86) | +0.269 (0.65) |
| `BM` | +0.063 (0.17) | +0.438 (0.81) | +1.650 (2.32) | +0.104 (0.38) | +0.428 (1.04) | +0.961 (1.66) |
| `Tax` | +0.333 (1.87) | +0.727 (3.13) | +0.605 (1.92) | +0.256 (1.92) | +0.309 (1.67) | +0.293 (1.23) |
| `PS` | +0.142 (0.27) | −0.831 (−1.45) | −0.666 (−0.81) | −0.213 (−0.41) | −0.883 (−1.59) | −1.116 (−1.40) |
| `DelEqu` | +0.180 (0.58) | +0.119 (0.24) | +0.174 (0.23) | +0.268 (1.73) | +0.304 (1.34) | +0.469 (1.34) |
| `NetDebtFinance` | −0.448 (−2.13) | −0.766 (−2.36) | −0.826 (−1.63) | +0.106 (0.84) | −0.005 (−0.03) | +0.054 (0.20) |
| `realestate` | +0.315 (1.04) | +0.340 (0.74) | +0.690 (0.98) | +0.086 (0.43) | +0.152 (0.50) | +0.050 (0.11) |

**Data sanity (reported only).** In every selected signal and file, `LS` equals the top port minus `01` to within 1×10⁻¹³ percentage points. The top port is `10`, or `05` where the screen file holds quintiles. `PS` has no `02` port in the VW file, because it is the discrete F-score.

## Disconfirmation

**Against retiring the idea:**
- Combination did what single signals did not. All six earlier single-signal CZ reads failed, but here five of six cells pass, the VW composite t is 1.07 to 1.81, and the worst 12-month loss is under 9% in every cell, against −27% to −36% for `XFIN` alone.
- The eight are nearly uncorrelated after 2009 (mean pairwise +0.018), so the diversification held out of sample.
- The one failing cell misses by 0.007 in t.

**For the verdict:**
- The frozen rule is all-six, and one cell fails.
- The edge is small against cost. The composite mean is below the #2828 L/S cost floor in four of six cells, including every screened cell (0.118 to 0.192 against 0.2436).
  - A pass on the 0.10 floor would still not have been net alpha.
- Two of the eight lose money after 2009, which is the decay the [ranking](2026-10-30-strategy-first-ranking.md) priors predict:
  - `PS` is negative in five of six cells.
  - `NetDebtFinance` is significantly negative in VW.
- The VW cells lean on `ShareIss1Y` and `Tax`. The 2020+ VW cell also leans on one `BM` run (+1.65 %/month).
- The t values ignore the selection over 73 signals (no deflation), and the 2016+ and 2020+ windows overlap 2009+.

## Caveats

**Pool rule.** The substitute rule was frozen before any return was read, but it is ours, not CZ's.
- `Cat.Data = Accounting` includes valuation ratios (`BM`, `EP`, `SP`). Those are slow but are a different family from "anomalies" in the narrow sense.
- It also includes quarterly earnings-surprise signals (`EarningsSurprise`, `roaq`), which are less slow.
- None of the quarterly ones was selected.

**Seen before the freeze.** The post-2009 returns of `ShareIss1Y` and `AssetGrowth`, `CompEquIss`, `CBOperProf`, `XFIN` and `NetEquityFinance` had been read in earlier desk reads.
- `ShareIss1Y` was selected, by its pre-2009 t.
- The rule forced their inclusion in the pool, and selection used only 1990–2008 data.

**Data.**
- Selection used only the VW file, and the same set was applied to the screen file.
- Neither file is TradePartner's $200M dollar-volume universe.
- CZ gross returns leave out our execution, borrow availability, taxes and financing.

**Windows.**
- The 2020+ window reaches into the [ADR 0016 point 8](../decisions/0016-development-boundary-and-forward-exams.md) exam interval. No registered store holdout is spent.
- For signals with a pre-1990 sample start, 1990–2008 is partly in-sample. That is acceptable for selection, but the pre-2009 t values are not post-publication evidence.

**Run discipline.**
- Before the real run, the script was exercised once on synthetic random data, in a scratch folder with no CZ file, to catch crashes. No real return was read.
- The script was edited only before its single real run.

## What a survivor's store test would have needed

None is proposed, because the composite failed. This is recorded for a later re-entry. A store test would need:
- point-in-time first-vintage statement fields for all eight signals (share count, net operating assets, book equity, tax expense, the F-score components, debt issuance, real-estate holdings);
- a composite turnover estimate;
- borrow checks on eight short legs;
- a new frozen registration that discloses this desk read.

## Exploratory-cut ledger

- **No extra return cuts.** Every figure above was named in the frozen comment, as a decision cell or as a reported-only line.
- **Structure probe.** Before the freeze, one structure probe, `~/tradepartner-probes/composite/structure.py`, selected `signalname`, `port` and `date` only, with no `ret`. Its findings are stated in the freeze.
- **Script.** `compute.py` was written after the freeze and run once on real data. There was no rerun and no edit after the run.

## UNVERIFIED

- That CZ's `Cat.Data = Accounting` signals all update at most quarterly.
- Composite turnover, and borrow availability for the eight short legs.
- Whether the composite survives a beta, size or value adjustment (the selection includes `BM` itself).
- Whether the paper-fill cost proxy applies to a monthly multi-signal book.

## Sources

- Chen–Zimmermann `PredictorAltPorts_DecilesVW.parquet` and `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet`, Hugging Face `OpenSourceAP/CrossSection` commit `ad1dfba5`, pinned by SHA-256 on #2888 (Tier 1 data, gross returns).
- CZ `SignalDoc.csv`, local copy of 2026-10-28, pinned by SHA-256.
- Repository records:
  - [Frozen kill line on #2888](https://github.com/josejuarez96/tradepartner/issues/2888#issuecomment-6583038848) and [verdict comment](https://github.com/josejuarez96/tradepartner/issues/2888#issuecomment-6583073976)
  - [XFIN desk read](2026-11-01-net-external-financing-desk-read.md), whose structure this read copies
  - [Strategy-search retro](../retros/2026-11-01-strategy-search-retro.md)
  - [Hypothesis backlog](hypothesis-backlog.md)
  - [Strategy-first ranking](2026-10-30-strategy-first-ranking.md)
  - [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md)

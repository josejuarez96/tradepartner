# Research Report: cash-flow value (CZ `cfp`), desk read

**Brief:** #2946, candidate 3 under the second strategy-search limit  ·  **Date:** 2026-11-03  ·  **Status:** COMPLETE  ·  **Agent/model:** team cfpread, claude-opus-5-5. It reads two Chen–Zimmermann parquets and the CZ `SignalDoc.csv` locally with DuckDB and Pandas, and reads nothing from the store.

## Answer

**Verdict: FAIL** (evidence grade for "cash-flow-to-price value, long-short, clears our cost floors with both legs positive at t ≥ 1 in every post-2009 cell": **NOT SUPPORTED**, confidence medium: all six cells fail, and the short leg fails in every one).

The rule is the [kill line frozen on #2946](https://github.com/josejuarez96/tradepartner/issues/2946#issuecomment-6608203413). It was posted at 2026-11-03 15:42:19 UTC, before any `cfp` return was read. The script's single run started at 15:42:52 UTC.

- `cfp` (operating cash flow to price, Desai, Rajgopal and Venkatachalam 2004) fails all six file × window cells.
- The short leg (the mean of all ports minus the low-`cfp` port) is +0.03 to +0.15 %/month with t at most 0.36, and negative in VW 2020+. It is below its floor in five cells and below t 1.0 in all six.
- The L/S t is 0.10 to 1.18, under 1.0 in five of six cells.
- The long leg is the only part with a signal: in the screened file it is +0.18 to +0.65 %/month with t 1.19 to 1.95. That is reported only and cannot rescue the verdict.

`cfp` is retired at rung 2. This read is **candidate 3** under the [second strategy-search limit](hypothesis-backlog.md).

## Evidence

The read can be reproduced from [compute.py](/Users/josejuarez/tradepartner-probes/cfp/compute.py) (sha256 `ab5c0417…77bf`) and its [out.txt](/Users/josejuarez/tradepartner-probes/cfp/out.txt) (sha256 `e62fe1ac…8cc8`). `out.txt` was written once and set read-only.

**Inputs.**
- The script checks every input against its frozen SHA-256 before reading it:
  - the VW parquet, `95409cb1…79a8`;
  - the screen parquet, `fcd2ae40…3734`;
  - `SignalDoc.csv`, `0ceb1102…0178`.
- These are the same files as #2828 and #2888.
- `SignalDoc` gives `cfp` Sign +1, so high `cfp` is the favourable side.
- `ret` is in percent per month.

**Structure (no `ret` read).** `cfp` is in both files, 738 months from 1964-07 to 2025-12 for every port, 204 months from 2009-01. VW holds deciles `01`..`10`; the screen file holds quintiles `01`..`05`. Both files end in 2025-12.

**Legs.**
- High port: `10` (VW) or `05` (screen). Low port: `01`.
- L/S is CZ's `LS` port. It equals high − `01` to within 1×10⁻¹³ percentage points in every cell (the frozen check allows 1×10⁻⁶).
- Long = high − the equal mean of all ports. Short = that mean − `01`.

**Floors.** The cost base is #2828's except turnover:
- 100% one-sided turnover per leg per year, each unit two trades;
- borrow 30 bp/year on the short leg;
- 2× margin over trading plus borrow.

| File | Cost per side | Long cost floor (reported) | Short floor | L/S floor |
|---|---:|---:|---:|---:|
| VW | 15 bp | 0.050 | 0.100 | 0.150 |
| NYSE-p20 screen | 25 bp | 0.0833 | 0.1333 | 0.2167 |

A cell passes only if:
- L/S and short means are at or above their floors;
- both legs are at or above +0.10 %/month;
- L/S, long and short each have t ≥ 1.0.

t is the mean over its ordinary standard error (ddof 1), with no HAC.

**Cells.** Mean %/month, with t in brackets.

| File | Window (months) | L/S | Long | Short | Cell | Failing tests |
|---|---|---:|---:|---:|---|---|
| VW | 2009+ (204) | +0.221 (0.58) | +0.122 (0.53) | +0.099 (0.30) | **FAIL** | short < 0.100; t of L/S, long, short < 1 |
| VW | 2016+ (120) | +0.268 (0.46) | +0.239 (0.82) | +0.029 (0.06) | **FAIL** | short < 0.100; t of L/S, long, short < 1 |
| VW | 2020+ (72) | +0.092 (0.10) | +0.431 (1.03) | −0.339 (−0.44) | **FAIL** | L/S < 0.150; short < 0.100; t of L/S, short < 1 |
| Screen | 2009+ (204) | +0.251 (0.84) | +0.181 (1.19) | +0.070 (0.36) | **FAIL** | short < 0.1333; t of L/S, short < 1 |
| Screen | 2016+ (120) | +0.419 (0.90) | +0.341 (1.49) | +0.077 (0.25) | **FAIL** | short < 0.1333; t of L/S, short < 1 |
| Screen | 2020+ (72) | +0.798 (1.18) | +0.652 (1.95) | +0.146 (0.33) | **FAIL** | short t < 1 |

**Literal rule application.**
- The script compared unrounded values against the exact floors.
- The closest call is VW 2009+, whose short mean is 0.0993 against 0.100. That cell also fails on three t floors, so no rounding changes it.
- The only cell that clears every mean floor is screen 2020+. It fails on the short leg's t of 0.33.
- No file, window, signal or threshold was chosen after the returns were seen.

**Reported only (cannot rescue or kill).**

| File | Window | L/S positive months | Worst 12m L/S | Long vs cost floor | 200% stress (long / short / L/S) |
|---|---|---:|---:|---|---|
| VW | 2009+ | 109/204 (53.4%) | −50.37% | above 0.050 | pass / fail / fail |
| VW | 2016+ | 67/120 (55.8%) | −50.37% | above | pass / fail / pass |
| VW | 2020+ | 42/72 (58.3%) | −50.37% | above | pass / fail / fail |
| Screen | 2009+ | 107/204 (52.5%) | −33.14% | above 0.0833 | pass / fail / fail |
| Screen | 2016+ | 67/120 (55.8%) | −33.14% | above | pass / fail / pass |
| Screen | 2020+ | 41/72 (56.9%) | −23.80% | above | pass / fail / pass |

The 200% stress floors are VW 0.100 / 0.150 / 0.250 and screen 0.1667 / 0.2167 / 0.3833 (long / short / L/S). The worst 12 months in VW, −50.4%, falls inside 2020+ and so appears in all three VW windows; the same holds for the screen's −33.1% in 2009+ and 2016+.

## Disconfirmation

**Against retiring the idea:**
- The long leg holds up. Above the market's equal port mean, high-`cfp` names earn +0.12 to +0.43 %/month in VW and +0.18 to +0.65 in the screen. Screen t is 1.19, 1.49 and 1.95, and every cell clears the long-leg cost floor, even at 200% turnover.
- The L/S mean is positive in all six cells and clears its cost floor in five of them (VW 2020+ is the exception).
- Value's long post-2007 drawdown (QI-14's "long dry periods") sits inside these windows, so a weak 2009+ read may say more about the era than about `cfp`.

**For the verdict:**
- The frozen rule needs both legs, and the short leg has no edge in any cell: t at most 0.36, negative in VW 2020+. The short leg is the half that carries borrow.
- L/S t is under 1.0 in five of six cells, and the L/S path is volatile: the worst 12 months are −50% (VW) and −33% (screen).
- The VW 2020+ cell, where value's rebound should show up, has an L/S of +0.09 with t 0.10. Its gain sits in the long leg, and the low-`cfp` decile outperformed the average.
- `cfp` gives the same picture as XFIN in #2828: a long leg that may carry something, a short leg that is noise.

## Caveats

**Seen before the freeze.**
- `BM` was one of #2888's eight signals, and its post-2009 CZ L/S was read there. That read was strong in VW 2020+ (+1.650 %/month, t 2.32), a period when `cfp` L/S is near zero. `cfp` is a sibling value ratio, so this was prior information when the candidate was picked.
- While checking that `cfp` had not been printed in #2888, this team saw one line of #2888's `out.txt`: `cfp` VW `LS` for 1990-01..2008-12, mean +0.185 %/month, t 0.53. That is the selection window, before every decision window, and it is stated in the freeze.
- The draft design (signal, cells, floors, legs, verdict rule) was written by the orchestrator before any `cfp` return was read. The freeze fixed only port labels, months, the `LS` check tolerance and the cost arithmetic.

**Floors.**
- The 100% turnover base is lower than #2828's 120% because value ratios rebalance about once a year. `cfp` turnover is UNVERIFIED. At 200% the short and L/S floors still fail in the same cells or more.
- The short leg would fail its t floor at any cost level, so the floors do not decide this verdict.

**Data.**
- The screen file holds quintiles, so its legs are coarser than VW's deciles.
- Neither file is TradePartner's $200M dollar-volume universe.
- CZ gross returns leave out our execution, borrow availability, taxes and financing.

**Windows.** The 2016+ and 2020+ windows overlap 2009+. The 2020+ window reaches into the [ADR 0016 point 8](../decisions/0016-development-boundary-and-forward-exams.md) exam interval. No registered store holdout is spent.

**Run discipline.**
- Before the real run, the script was exercised once on synthetic random data, in a scratch folder with no CZ file, to catch crashes. No real return was read.
- The script was edited only before its single real run.

## What a survivor's store test would have needed

None is proposed, because `cfp` failed. This is recorded for a later re-entry. A store test would need:
- point-in-time first-vintage operating cash flow, with market value taken at the cash-flow `known_at`;
- a turnover estimate for annual rebalancing;
- borrow checks on the low-`cfp` short leg;
- a new frozen registration that discloses this desk read.

A long-only `cfp` book would be a different candidate, with its own kill line written before any read. The long-leg numbers above would then have to be disclosed as already seen.

## Exploratory-cut ledger

- **No extra return cuts.** Every figure above was named in the frozen comment, as a decision cell or as a reported-only line.
- **Structure probe.** Before the freeze, one structure probe, `~/tradepartner-probes/cfp/structure.py`, selected `signalname`, `port` and `date` only, with no `ret`. A follow-up line read `SignalDoc.csv` metadata (Sign, category, sample years) for `cfp` and `BM`. Both outputs are in `structure.out`.
- **Script.** `compute.py` was written after the freeze and run once on real data. There was no rerun and no edit after the run.

## UNVERIFIED

- `cfp` turnover per leg per year.
- Borrow availability and cost for the low-`cfp` decile.
- Whether the long-leg result survives a beta, size or `BM` adjustment.
- The correlation of `cfp` with `BM` after 2009 (not computed; it was not in the freeze).

## Sources

- Chen–Zimmermann `PredictorAltPorts_DecilesVW.parquet` and `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet`, Hugging Face `OpenSourceAP/CrossSection` commit `ad1dfba5`, pinned by SHA-256 on #2946 (Tier 1 data, gross returns).
- CZ `SignalDoc.csv`, local copy of 2026-10-28, pinned by SHA-256.
- Desai, Rajgopal and Venkatachalam (2004), "Value-glamour and accruals mispricing: one anomaly or two?", *The Accounting Review* (the `cfp` source paper, per `SignalDoc.csv`).
- Repository records:
  - [Frozen kill line on #2946](https://github.com/josejuarez96/tradepartner/issues/2946#issuecomment-6608203413) and [verdict comment](https://github.com/josejuarez96/tradepartner/issues/2946#issuecomment-6608225924)
  - [Composite desk read](2026-11-02-composite-weak-signals-desk-read.md), whose structure this read copies
  - [XFIN desk read](2026-11-01-net-external-financing-desk-read.md) (cost base and leg definitions)
  - [Hypothesis backlog](hypothesis-backlog.md) and [claims.toml](claims.toml) (QI-14, LS-5, LS-1, QI-16)
  - [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md)

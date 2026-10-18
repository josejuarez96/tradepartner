# B9 killed, B3 retired, IRRX parked (owner, 2026-10-18)

**Date:** 2026-10-18  ·  **Issue:** #1765  ·  **Decided by:** the owner, in chat with the orchestrator, from the strategy pass of the same day  ·  **Kind:** desk note, arithmetic on graded evidence; no run, no trial, no store read.

Three verdicts. Each states its criterion, the numbers, the sources and what it closes.

## 1. B9 (earnings-reaction drift) is killed at rungs 1 and 2

**Criterion (ADR 0023 kill ladder, fixed before the numbers were read for this note).**
- Rung 2: kill if the published long leg after 2010 is below 0.10 %/month **or** its t-statistic is below 1.0. This is the line the ADR applies to IRRX.
- Rung 1: kill if the break-even cost per side is not above 6 bp. Break-even per side = long-leg gross per month / (2 x one-sided turnover per month).

**Yardstick.** The size-screened Chen-Zimmermann `AnnouncementReturn` long leg (2010-2024, excluding names below the NYSE 20th size percentile), the worse of the two published files and the nearer to our top-1,000 equal-weight universe's small end. The value-weighted file (long leg +0.32 %/month, t 1.88) was **not** used as the yardstick. Choosing it after seeing both files would be a forking-path choice, so it is reported here and not relied on.

| Series (2010-2024, long leg, gross) | Return | t | Rung 2 (>= 0.10 %/mo and t >= 1.0) |
|---|---|---|---|
| Size-screened (ex NYSE p20) | +0.07 %/mo | 0.94 | **fails both thresholds** |
| Value-weighted deciles (reported, not used) | +0.32 %/mo | 1.88 | would pass |
| 2019-2023 alone | insignificant in both files | | |

**Rung 1 arithmetic.** B9's one-sided turnover is 100-200 %/month (hypothesis file; backlog prior). On the size-screened leg, 0.07 %/mo = 7 bp/mo, so break-even = 7 / (2 x 1.0) = 3.5 bp per side at 100 %/mo turnover and 7 / (2 x 2.0) = **1.75 bp per side** at 200 %. Both are below the 6 bp line. For scale, Probe 3's median open-fill slippage was +6.2 bp (n = 4, range -9.3 to +34.7; P3-2, P3-5) and the HO-14 floor is 5-10 bp plus half the spread (UNGRADED), so the measured cost is at or above the line even before the half-spread. The value-weighted leg would give 8-16 bp, which is why the yardstick choice mattered and why it is stated above.

**Verdict.** B9 fails rung 2 and rung 1 as written. It is killed. Rungs 3 to 5 are not run.

**Sources.** SH-6 (MIXED) in `claims.toml`, from the [short-horizon report](2026-10-09-short-horizon-candidates.md); SH-5 (NOT SUPPORTED, surprise-based PEAD) and SH-2 (costs) against; backlog B9 row and item; [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) rungs 1-2; the strategy pass of 2026-10-18.

**What it closes.**
- B9's hypothesis file (`docs/hypotheses/b9-earnings-reaction-drift.md`) is left as filed: it was never registered and a pre-registration is not edited.
- No `earnings_drift` root family, spec amendment, event-path engine task or paper book is built for B9.
- T164e's B9 reason (the ADR 0023 gate, "B9's kill-ladder rung 1") is gone. T164e remains on the plan only if another consumer wants the 8-K `items` store (IRRX needs it, and is itself parked behind the T198 sitting, see section 3). The orchestrator decides whether the plan line gets a note (plan edits are class B and are not made here).
- The fast book that could have carried a forward exam (B9's unspent `earnings_drift` holdout) no longer exists; a forward exam needs a new root family from a new idea that survives the ladder.

## 2. B3 (gross profitability) is retired by its own retirement rule

**Rule (`docs/hypotheses/b3-gross-profitability.md`, "Retirement condition", stated before any run).** B3 retires when the first `ok`, non-synthetic, in-sample trial over [2020-08-31, 2023-12-29] that passed `quant-auditor` has a base-level net `excess_cagr_spy` below **-1 pp/yr** **and** a run-time `dsr_excess` below **0.5**.

**The trial.** Trial 3 (hypothesis 2, `b3-gross-profitability`), in-sample, 40 monthly returns, run on the real store 2026-10-07 at code bce1a28 (T85f, #1156, PR #1158). The registry has exactly one trial for the hypothesis (N = 1) and no holdout row. `quant-auditor: PASS` on that trial, no blocker or should-fix (PR #1158).

| Reading (15 bp/side) | Value | Rule line | Met |
|---|---|---|---|
| Net excess CAGR over SPY, base level | **-3.28 pp/yr** (CAGR 7.78 % vs SPY 11.06 %) | below -1 pp/yr | yes |
| `dsr_excess` (psr basis, N = 1) | **0.3069** | below 0.5 | yes |
| Context, not part of the rule | DSR 0.7966; Sharpe 0.457 vs SPY 0.672; max drawdown -37.1 %; monthly turnover 10.4 %; excess at 0 bp -2.93 pp/yr; at 30 bp -3.62 | | |

Beta-adjusted (B11, 2026-10-10): alpha -3.2 pp/yr, beta 1.08, R² 0.84, so the trial is close to an index and its alpha is not significant either way. B11 is context, not a rule input.

**Verdict.** Both halves are met. B3 retires as a candidate for live capital. Per the file, retirement ends the line: a later revisit is a new hypothesis with its own registration and budget (research-program section 6).

**Caveat recorded.** The `restamp-1382a/b` repair releases will re-run trial 3 on a repaired store (work-map: "B3 trial 3 re-run"). The rule reads the first audited trial and lets a later one replace it only when an audit logged a bug in the earlier one. Trial 3 had no audit bug, so the reading stands. If the re-run moves excess above -1 pp/yr or `dsr_excess` to 0.5 or more, that is a data-vintage event for the owner to look at, not an automatic un-retirement.

**What it closes.**
- **B3b** (cash profitability, `basis = "cash"`): its own kill is B3's rule read on its own first trial, and it is the same family with the same index-like construction. It is not run; it stays a retired variant, not a new idea.
- **B4** (momentum + profitability): the sweep file `docs/sweeps/b4-momentum-profitability-combined.md` pins its kill line at B3 trial 3's -3.28 pp/yr and was parked by the owner on 2026-10-08 (#1292). With B3 retired the combination's second leg is gone; B4 is dropped. The sweep was never registered on the owner's store.
- B3's deferred paper book (ADR 0023 D5) is not needed; its slot stays with fast survivors.

**Sources.** PR #1158 (T85f evidence and auditor verdict); `docs/hypotheses/b3-gross-profitability.md`; `docs/sweeps/b4-momentum-profitability-combined.md`; [B11 note](2026-10-10-b11-beta-alpha-split.md); QI-6 SUPPORTED (narrow), QI-16 MIXED in `claims.toml`; the strategy pass of 2026-10-18.

## 3. IRRX is parked behind T198

**Criterion (ADR 0023 rung 2, pre-stated in the #1734 brief).** Kill if the 2010-2024 long-only leg is under 0.10 %/month or t is under 1.0; if no usable published long leg exists, IRRX is **parked** behind T198, neither killed nor passed.

**Finding.** The Dai, Medhat, Novy-Marx and Rizova (FAJ 2024) paper reports only winner-minus-loser spreads for REV, IRR and IRRX (long-short 0.58 %/month, t 3.29, May 2001 to December 2021), with no loser-side leg and no post-2010 split. Chen-Zimmermann, JKP and Ken French carry no industry-relative or announcement-adjusted reversal series. The plain-reversal proxy's 2010-2024 long leg is -0.16 %/month (t -0.59), but IRRX and plain reversal diverge after 2001, so the proxy cannot decide the rung. Long-leg number: none published; evidence grade **INSUFFICIENT**. Full report: [IRRX rung-2 retry](2026-10-18-irrx-rung2-retry.md) (earlier attempt: [#1734](2026-10-18-irrx-long-leg.md)).

**Verdict.** Parked. Next step when T198 lands: build the long leg on our own store (rung 4; it needs the 8-K dates of T164e and only reaches back to 2016). Nothing is built for IRRX before then.

**Capacity note (rung 3 preview).** About 98 names fit $100 under D6's feasibility rule; a top 20-50 subset on fractional orders fits.

## 4. Formal retirement mechanism

`tradepartner sweep retire <slug> --reason TEXT` (`backtest.promotion.retire`) retires **sweeps** only: it looks the slug up in the sweep registry and appends a `sweep_retired` decision. B3 is a hypothesis registered before the strategy lab (hypothesis 2, grandfathered), B9 and B4 were never registered, and no command retires a hypothesis. So no store write exists for these verdicts: this note and the backlog statuses are the record. Nothing was run against the store.

## Effect on the queue

The backlog's priority note order B10 -> B9 -> IRRX becomes **B10 first; IRRX parked behind T198; B9 killed; B3 retired**. Ladder items still open: B10 (rungs 1, 3, 4), then any new idea.

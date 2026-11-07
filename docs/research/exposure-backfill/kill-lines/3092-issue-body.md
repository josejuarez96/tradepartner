Research team `gatecal`, 2026-11-06. Source documents (local, not in git): `~/tradepartner-probes/arch/99-synthesis.md` (orchestrator synthesis of R1-R4) and `~/tradepartner-probes/arch/R4-comparison.md` §4.

## Why (synthesis, in brief)

- R4 answered R3's decisive question: the six failed candidates were **not** killed by costs. Store trials were negative gross (beta and size of long-only books vs SPY); CZ desk reads had gross spreads above cost and died on rung 2's both-legs, all-windows conjunction.
- R4's iid simulation: the rung-2 gate passes a real IR-0.5-per-leg signal about 17% of the time, about 3% when one leg is dead.
- Recommendation order: (1) calibrate the gate, (2) score long-only books against a matched benchmark, (3) run R4's pooled CZ long-leg composite experiment, (4) Audit A fixes before any new store backtest is believed.
- **This issue covers step 1 now (gate calibration) and freezes step 3's kill line before any computation.** Step 3 is not run under this issue yet.

## Frozen design and lines, verbatim

### From the synthesis, Recommendation

1. **Calibrate the gate**, about 1 agent-day: a block-bootstrap planted-signal study of the current rung-2 rule against a single-window t ≥ 2 rule at matched false-discovery rates (R4 §4). If the current rule's true-positive rate is under 25% where the alternative exceeds 50%, amend rung 2 to the calibrated rule.
2. **Score long-only books against a matched benchmark.** Use a beta- and size-matched benchmark, or report alpha after a market and size regression alongside raw excess over SPY. That way a kill measures the signal, not the tilt.
3. **Run R4's experiment**, about 5 agent-days, with no store and no holdout. R3's four arms run on the pinned Chen-Zimmermann files, walk-forward 2000-2025. Each signal counts only after its original sample ended. The decision cell is the no-gate 1/N long-leg composite, net, over 2000-2019.
   - **Pass** needs t ≥ 2 in both files, t ≥ 1 at 2x cost, and a win over a fixed 5-signal baseline. Then the minimal build follows: an N-signal `signals_combined` and one paper book.
   - **Kill** if t < 1 or it does not beat the baseline. That retires published slow signals, single or combined, as a source. FL-MC is not built, and the program's effort moves to own-data event features (the idea-card pilot).
4. **Audit A fixes land before any new store backtest is believed.** That covers the delisting and halt exit price, chain-row validation, fill on as-known bars, and the benchmark dividend.

The kill line for step 3 is frozen as written above and in R4 §4. It gets posted on its issue before any computation, per the kill-line rule.

### From R4 §4.2 Design


**Data.** The two pinned CZ parquets (VW `95409cb1…79a8`, screen `fcd2ae40…3734`) and `SignalDoc.csv` (`0ceb1102…0178`), the same files as #2828, #2888 and #2946. No store read, no holdout, no exam months spent.

**Pool.** The #2888 pool rule, frozen as written: `Cat.Data = Accounting` with `SampleEndYear ≤ 2005`, plus `CBOperProf`, giving 80 signals. A signal is **eligible in year Y only if `SampleEndYear < Y`**, so every evaluated month is out of the original sample (McLean-Pontiff's OOS).

**Walk-forward.** For each year Y from 2000 to 2025, weights and gates use months up to the end of Y−1 only, and the book is held through Y. That gives 312 OOS months.

**Legs.**
- *Primary:* the long leg, defined as the high port minus the equal mean of ports (the desk reads' definition).
- *Secondary:* L/S.

**Costs.** The #2828 base: 120% one-sided turnover per leg per year, 15 bp a side for VW and 25 bp for the screen file, 30 bp borrow on short legs, **no 2× margin**. Every arm is also reported at 2× cost.
- A composite is charged the *sum* of its components' turnover. That gives no netting credit, so it is a lower bound on net.

**Arms (one combiner throughout: equal weight across admitted signals, re-formed yearly):**
1. **Current gate.** Admit a signal in year Y if its trailing data passes a replica of the rung-2 rule: both legs and L/S, three trailing windows (204/120/72 months), mean floors and t ≥ 1.
2. **Shrinkage gate.** Admit if the trailing full-history t of the net long leg is ≥ 1.0, with the #2888 correlation cap (|ρ| ≤ 0.30, ordered by t).
3. **No gate.** All eligible signals at 1/N (DeMiguel 2009's baseline).
4. **Dumb baseline.** A frozen 5-signal set chosen today by name, not by returns: `BM`, `GP`, `AssetGrowth`, `ShareIss1Y`, `Accruals`, each entering at its eligibility year.

**Gate calibration (R3's matched-FDR requirement).** On the same panel, build 400 synthetic signals:
- *Nulls:* real eligible long-leg series, 12-month block-shuffled in time.
- *Planted:* the same block-shuffled series plus a known drift at a net annual IR of 0.3, 0.5 or 0.8 (20% of the series).

This keeps the real volatility, fat tails and cross-correlation, which answers R3 §3.1. Score arm 1's gate, arm 2's gate and a single-window t ≥ 2.0 rule by their true-positive rate at a false-discovery rate of 10% and 20%, over 50 seeds. The synthetic series are labelled `synthetic` and never counted as real exposures.

**Pre-registration.** Post the kill line (below), the pool list, the pins and the script's SHA-256 on a new issue before any return is read. Record one exposure per launch with `tradepartner exposure record --source open_data` (`record_exposure`, `store/research.py:1522`). Disclose the signals whose post-2009 returns are already seen: AssetGrowth, CompEquIss, ShareIss1Y, NetEquityFinance, CBOperProf, OperProfRD, XFIN, cfp, AnnouncementReturn and the composite's eight. Under the idea-pipeline's source-A rule, their 2020+ months are not clean, so the experiment reports 2000-2019 and 2020-2025 separately, and **only 2000-2019 decides**.

### From R4 §4.3 Pre-registered success criterion and kill line


The decision cell is the **arm 3 long-leg composite, net at 1× cost, over 2000-2019 (240 months)**, read in both files. Arm 3 is chosen because it has no fitted choices.

**PASS (combination is worth one build)**, all three required:
1. Arm 3 net long-leg mean > 0 with t ≥ 2.0 in **both** files.
2. Arm 3 is still t ≥ 1.0 at 2× cost in both files.
3. Arm 3 beats arm 4 on the paired monthly difference with a point estimate > 0 in both files.

Supplementary: the gate result passes if arm 2's true-positive rate is at least arm 1's plus 20 percentage points at both false-discovery rates and at IR 0.5.

**KILL (posted before computing)**, either is enough:
- Arm 3's net long-leg t < 1.0 in either file over 2000-2019.
- Arm 3 does not beat arm 4 in either file.

Either outcome retires "published slow accounting signals, single or combined, long leg" as a candidate source (a `TP-` claim). The second limit's remaining slots then go only to cards from sources B and C, and to cost work. No FL-MC component is built.

**Gate-only consequence.** If, at a planted IR of 0.5, arm 1's true-positive rate is below 25% while the single-window t ≥ 2 rule exceeds 50% at the same false-discovery rate, the rung-2 rule is amended (a class-B ADR 0023 amendment, the owner's word). This holds whatever the arms show.

**Inconclusive.** Anything between PASS and KILL: keep today's process and spend no more on architecture, as R2's own default says.

**Power check, stated now.** Over 240 months, t ≥ 2 at 80% power needs an annual IR of about 0.63 (from R3's §2.2 table scaled to 20 years). Chen-Velikov's combination ceiling of about 20 bp a month L/S is roughly at that line on the long leg alone. So a PASS is possible but not likely, and a KILL is informative.


## Gate-amendment trigger (restated for this issue)

The trigger is R4 §4.3's "Gate-only consequence" above, unchanged: at a planted IR of 0.5, if the current rung-2 rule's true-positive rate is **below 25%** while the single-window t ≥ 2 rule's exceeds **50%** at the same false-discovery rate, the rung-2 rule is amended (class-B ADR 0023 amendment, the owner's word).

## Step-1 calibration as run (stated before computation)

- Data: the pinned CZ files named above, sha256-verified before use; SignalDoc for the pool. No store, no holdout, no exam decision.
- Synthetic series only: each signal's real long and short leg months (both files jointly, same block indices, so cross-file and cross-leg correlation and fat tails survive) are **demeaned**, then circular-block-bootstrapped (12-month blocks) to 204 months. Planted signals add a drift of annual IR 0.25 / 0.5 / 0.75 / 1.0 per live leg, scaled to that leg's own volatility: both legs live, long live with short dead, short live with long dead. All labelled `synthetic`, never counted as real exposures.
- Rules scored: (a) the current rung-2 rule as practised on #2828/#2946 (L/S, long, short each with mean floor and t ≥ 1 in 2 files × 204/120/72-month windows), plus the ADR 0023 line-40 literal reading (long leg only) as a sensitivity row; (b) single-window t ≥ 2 (VW L/S, 204 months); (c) one justified alternative (in the report).
- TPR and FPR per cell, FDR at a 20% planted prior (R4's mix), and TPR at matched FDR 10% and 20% by tuning each rule's t threshold. Fixed seeds, ≥ 2000 reps per cell.
- Not done here: `tradepartner exposure record` (it writes the real store, which this team may not touch). The orchestrator records it if wanted.

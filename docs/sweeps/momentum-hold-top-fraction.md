# Sweep: H1 momentum, the buy/hold buffer, top 10% / hold 20% (one variant)

<!--
Merging this file does not register it. The owner (or the orchestrator on his store) runs
`tradepartner sweep register docs/sweeps/momentum-hold-top-fraction.md` after merge, then
`tradepartner sweep run momentum-hold-top-fraction` (strategy-lab spec req 1 and 2;
backtest spec amendment #657). The file is frozen once registered. The rung-1 cost note
the spec's open question 32 requires was posted on #657 before this file (2026-10-20).
-->

**Family:** momentum  ·  **Author:** team kilobufsweep (agent draft); the one-variant grid by Jose Juarez on #1957 (2026-10-19)  ·  **Date:** 2026-10-20  ·  **Issue:** #1956

This is step 3 of the owner's 2026-10-19 order on #657: after T238 and T239 are ticked,
the `momentum` sweep file that grids the buy/hold buffer, `strategy.hold_top_fraction`. It
is a **one-value grid**, because the owner accepted one trial in `momentum`'s N (backtest
spec amendment #657, "Trial count"; open question 31, decided 2026-10-19 on #1957).

## Economic rationale for the axis and its value

**The question.** Does H1's book, still bought into the top tenth of the ranked universe but
now held while a name ranks inside the top fifth, do better net of costs than H1 over the
same months? H1 ([h1-momentum-12-1](../hypotheses/h1-momentum-12-1.md)) fixes the top tenth
at a monthly rebalance with no buffer. [G1](../research/2026-09-25-g1-momentum-post-2010.md)
and H1 name a buy/hold buffer as a **second hypothesis in the `momentum` family**, not a
variant of H1, and the backtest spec's [#657
amendment](../specs/backtest.md#amendment-2026-10-19-657-the-momentum-buyhold-buffer-strategyhold_top_fraction)
adds the key, its rule and this file. The buffer is a rule of `equal` weighting: at plan i
the targets are `ranked[:k]` for `k = top_fraction` plus every name already held that still
ranks inside `h = hold_top_fraction`; each target gets the common equal weight. A held name
that falls outside the top fifth is sold. `n_retained` counts the names kept by the buffer.

- **`strategy.hold_top_fraction = [0.20]`, at `strategy.top_fraction = 0.10`**: Novy-Marx &
  Velikov (RFS 2016, NBER w20721, Table 3 Panel B, via G1), a value-weighted decile
  long-short book, found a 10 %/20 % buy/hold rule cut one-sided turnover from 34.52 % to
  18.82 %/month and raised the net return from 0.68 to 0.85 %/month (1963-2012). That
  published 10/20 pair is the one value with evidence, so it is the one variant. Owner
  decision on #1957 (open question 31, 2026-10-19): one variant, `top_fraction = 0.10`,
  `hold_top_fraction = 0.20`. No other value is a counted trial.
- The key's own rules (`StrategyConfig`, spec amendment #657 item 1) make a second variant
  pointless here. A value below `top_fraction` is refused naming both keys. A value **equal**
  to `top_fraction` is normalised to `0.0`, so a "no buffer" point is H1's own frozen set and
  its fingerprint is already registered: req 1(b) refuses it. The buffer has no
  `lab.axis_lattice` entry (spec amendment #657 item 1), so no continuous axis is implied.

1 × 1 = **1 variant** in **1 read group**, far under `lab.max_variants_per_sweep` (100).
Like H1, `weighting = "equal"`; `hold_top_fraction` is a rule of `equal` weighting only
(a `hold` weighting has its own exit rule and refuses the buffer).

## Parameters

The fixed values are H1's family rules (the `momentum` family's first registration). A
frozen key this block leaves out is read from live settings at registration and refused
there if it differs from the family rules (req 1(a)). As the first sweep pins them
([momentum-topfrac-cadence](momentum-topfrac-cadence.md), "Parameters"), the block pins the
owner-edited exception lists `universe.accepted_price_jumps`,
`universe.accepted_same_day_pairs`, `universe.accepted_shares_facts` and
`master.keep_successors` to the momentum family rules' values, so a later `.env` entry
cannot move this file's frozen set.

```toml sweep
slug = "momentum-hold-top-fraction"
family = "momentum"
title = "H1 momentum: the buy/hold buffer, top 10% / hold 20% (one variant)"
in_sample_start = 2020-08-31

[holdout]
start = 2024-01-01
end = 2026-09-30

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
weighting = "equal"
signal_total_return = true
# hold_top_fraction is the one-value grid axis below

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
rebalance_cadence = "month_end"
signal_anchor = "month_end"

[universe]
accepted_price_jumps = []
accepted_same_day_pairs = ["0001903392@2024-03-14"]
accepted_shares_facts = []

[master]
keep_successors = ["0000891103@2020-08-10"]

[grid]
"strategy.hold_top_fraction" = [0.20]

[lab]
selection_statistic = "excess_cagr_spy"
expected_excess_cagr_spy_pp = 0.0
expected_range_pp = [-1.0, 1.0]
promote_at_least = 0.5
retire_below = -0.01
```

Where each value comes from. Every value is taken from H1's family rules or the
strategy-lab spec's defaults, as the owner decided (structural reading of #1957):
- `in_sample_start`, `[holdout]`, `[strategy]`, `[costs]`: H1's `toml hypothesis` block,
  the `momentum` family rules (req 1(a)). `[holdout]` is the family's already-spent exam
  (ADR 0023), never read by this sweep: `sweep run` spends no holdout (req 2, req 5(a)).
- `schedule.rebalance_cadence = "month_end"` and `schedule.signal_anchor = "month_end"`:
  H1's rules. The buffer's retention rule is applied once per rebalance, so the cadence is
  H1's monthly one, not a grid axis; a cadence axis would be a second, unaccepted grid.
- `[universe]` and `[master]` pins: the momentum family rules' values, as the first sweep
  pins them.
- `[grid] "strategy.hold_top_fraction" = [0.20]`: the owner's one value on #1957 (open
  question 31). The pair 10/20 is Novy-Marx & Velikov's.
- `[lab] expected_excess_cagr_spy_pp = 0.0` and `expected_range_pp = [-1.0, 1.0]`: H1's
  "Expected magnitudes": prior centred on **0 pp/yr**, plausible ten-year range **−1 to
  +1 pp/yr** (H1's file). Both are in percentage points, as the key names say. The buffer's
  published gain is an improvement **within** momentum, net over the unbuffered book, not a
  claim of positive excess over SPY, so the prior over SPY stays H1's.
- `[lab] selection_statistic = "excess_cagr_spy"` and `retire_below = -0.01`: H1's track
  record and retirement line, exactly as the first sweep chose them
  ([momentum-topfrac-cadence](momentum-topfrac-cadence.md), "Where each value comes from").
  H1's retirement reads the base-level `excess_cagr_spy` (below −1 pp/yr) and `dsr_excess`
  (below 0.5); the sweep ranks its argmax by `excess_cagr_spy`, so `retire_below` is that
  statistic's floor, and `excess_cagr_spy` is stored as a fraction (`metrics.py`): −1 pp/yr
  is written −0.01.
- `[lab] promote_at_least = 0.5`: the spec's `lab.promotion_min_dsr_excess` default (0.5),
  the lowest the parser accepts, and also H1's `dsr_excess` half of its retirement rule.

## Expected magnitudes and red flags for the sweep

- **Excess return over SPY (base cost level, 15 bp per side):** the one variant's prior is
  H1's, centred on 0 pp/yr in a −1 to +1 pp/yr range. The in-sample window is 3.3 years and
  momentum's single-year gaps run ±13 to 18 pp (H1's file), so expect the variant **outside**
  the range. H1's own in-sample trial on the store already sits at −5.8 pp/yr (disclosure
  below). The buffer is not expected to change the sign of that; Novy-Marx & Velikov's gain
  is a small net improvement (0.68 to 0.85 %/month) from lower turnover, mostly visible as
  **cost saved**, not as a large excess.
- **Turnover and cost:** the buffer should trade **less** than H1. H1's in-sample
  `turnover_monthly` is 32.4 % (trial 2); the buffered book's should be below it, the
  saving showing in `trial_rebalances.cost_paid` and the period turnover metric. `n_retained`
  is reported only when the buffer is on (spec amendment #657 item 2).
- **Red flags** (a prompt for a look-ahead, cadence-path or cost audit, never a gate):
  - The variant's base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp`
    (3.0 pp): spec req 15 marks it `red_flag`.
  - The buffered book beating H1 trial 2 by more than the whole prior range (2 pp/yr) net
    of costs. The buffer only re-weights and retains; a large gain points at the retention
    path, not at an edge.
  - `turnover_monthly` **above** H1's 32.4 %. Retention resets every target to the common
    weight, so a buffered book could in principle trade more; a turnover above H1's is
    unexpected and points at the retention rule, not at the buffer's cost saving.
  - `n_retained` zero every rebalance: the buffer never binds, so the variant is H1 in all
    but its frozen set. (Its fingerprint still differs, so the run is a distinct trial.)
  - `n_targets − n_retained` not equal to `k` at every rebalance, where `k` is the count
    `top_fraction` selects (`n_retained = |targets_i ∖ ranked[:k]|`, spec amendment #657
    item 2). The top-k ranked names are always targets, so the difference must be `k`; any
    other value means the top-k names were not all bought or the count is wrong.
  - Any variant with a `kind=holdout` trial. `sweep run` cannot spend the holdout (req 2,
    req 5(a)).

## Power arithmetic per variant count

The `momentum` family's N before this sweep is **11**: H1's two `ok` in-sample trials (trial
2 carries `n_trials = 2`; H1's holdout trial does not count), the first sweep's six `ok`
variants (T114, run 4, trials 11-16), the daily shakedown's one `ok` in-sample trial (trial
17; [paper plan](../plans/paper-trading.md), T160), and the long/short spike's two
configurations (T218, a registered exploratory `return` run in `momentum`, `ok`, counted by
research-registry req 9 as `research.family_run_count`; [report](../research/2026-10-20-long-short-spike.md)).
After this sweep, with its one variant `ok`, **N = 12**. V comes from the distinct
(parameter hash, window) pairs; the family's V is read at registration and its floor is
`lab.min_sharpe_variance_annual` (0.04, a standard deviation of 0.2 in annual Sharpe). At
N = 12 the expected maximum Sharpe of a null grid is about
`√V · ((1 − γ)·Φ⁻¹(1 − 1/12) + γ·Φ⁻¹(1 − 1/(12e)))` ≈ **1.66 · √V**: about **0.33** annual
at the V floor, and more if the family's real spread is wider.

The excess Sharpe has an 8.4 % tracking error (H1's figure) over 40 monthly returns. A
variant needs an annual excess Sharpe at or above SR* for its `dsr_excess` to reach 0.5,
roughly **+2.8 pp/yr** of excess at the V floor. That is nearly three times the top of the
prior range. **The sweep cannot separate an edge inside the prior from selection.** A
promotion at `promote_at_least = 0.5` is reachable only by a result well outside H1's prior,
which would read as a red flag before it read as an edge. The sweep's real product is the
buffer's turnover and cost answer, not a promotion.

## Prior-evidence disclosure

Results already seen before registration:
1. **Novy-Marx & Velikov's published 10/20 buffer figures** (via G1, and quoted in the
   spec amendment): turnover 34.52 % → 18.82 %/month and net 0.68 → 0.85 %/month, a
   **value-weighted decile long-short** book, 1963-2012. It is the only buffer result seen;
   it is net of Novy-Marx & Velikov's own cost model, not of this engine's 15 bp per side.
2. **The rung-1 cost note on [#657](https://github.com/josejuarez96/tradepartner/issues/657)**
   (spec open question 32, 2026-10-20, computed by a command from the configured
   `CostsConfig().per_side_bps` = 15 bp per side): the buffer saves 4.71 bp/month of cost
   (31.4 bp/month at 100 bp/side) against the published long-short turnover. The note states
   the 18.8 % is the published book's, not H1's, and that H1's own turnover needs the engine
   (T239).
3. **H1's in-sample trials on the owner's store** (2020-08-31 to 2023-12-29, the same window
   this variant runs): trial 2 has base-level `excess_cagr_spy` −5.77 pp/yr,
   `sharpe_annual_excess_spy` −0.27, `turnover_monthly` 32.4 %; trial 1 is nearly identical
   (−5.95 pp/yr). H1 is the unbuffered centre this variant is a buffer off.
4. **H1's holdout trial** (trial 4, 2023-12-29 to 2026-09-30): +4.44 pp/yr over SPY. The
   holdout is spent and this sweep never runs on it, but its result has been seen.
5. **The first sweep's six variants** (T114, trials 11-16, retired 2026-10-10): all lose to
   SPY, −2.6 to −6.4 %/yr. Same family, same window, different axes; disclosed because seen.
6. **The daily shakedown's operations book** ([momentum-daily-shakedown](../hypotheses/momentum-daily-shakedown.md),
   T160, trial 17): a 12-1 momentum book at `top_fraction = 0.20`, `daily`, run once over the
   default window as a machine test, −6.13 pp/yr in sample. Same family and window; disclosed
   because seen.
7. **The long/short spike** ([report](../research/2026-10-20-long-short-spike.md), T218,
   2026-10-19): a registered exploratory `return` run on H1's ranking, the same window, two
   configurations (a 50/50 long/short book and a SPY-hedged control). The net spread at 30 bp
   borrow is −6.82 pp/yr and the pre-declared kill rule holds, so the long/short `momentum`
   idea is killed. Same family and window; disclosed because seen, and counted in the family's
   N above.
8. **Ladder ledger disclosure (rule L3).** The mechanism here is the published buy/hold
   buffer, taken from H1 and G1's evidence, not a rung 1-3 screen; the
   [ladder ledger](../research/hypothesis-backlog.md#ladder-ledger) names no row for it
   (its rows are B9, IRRX, Q1 and L-1). There is therefore no ledger look or batch
   denominator to disclose for this mechanism.

No result for this grid (a buffered 10/20 book on this store) has been seen. Every grid and
`[lab]` value was fixed before any run; the fixed block is H1's, from the family rules that
are already registered.

## Retirement condition

The sweep retires (`tradepartner sweep retire momentum-hold-top-fraction --reason ...`) and
promotes nothing when its argmax, the variant's base-level `excess_cagr_spy`, is below
**−1 pp/yr** (`retire_below = -0.01`). That is H1's own retirement line applied to the one
variant. The sweep also promotes nothing while the variant is `red_flag`ged, until the audit
that flag opens has cleared it. A variant reaches anything beyond this report only through
`sweep promote` and its rules (spec req 4), never through the report alone.

# Sweep: H1 momentum, top fraction × rebalance cadence (first sweep, T114)

<!--
Merging this file does not register it. The owner (or the orchestrator on his store) runs
`tradepartner sweep register docs/sweeps/momentum-topfrac-cadence.md` after merge, then
`tradepartner sweep run momentum-topfrac-cadence` (strategy-lab spec req 1 and 2;
docs/plans/strategy-lab.md T114). The file is frozen once registered.
-->

**Family:** momentum  ·  **Author:** team sweepfirst (agent draft); axis values and `[lab]` choices by Jose Juarez on #1295  ·  **Date:** 2026-10-08

This is the strategy lab's first sweep and the plan's measurement run (T114). It does two
jobs. It measures seconds per variant at each cadence (`month_end`, `week_end`, `daily`),
seconds per variant in a two-variant read group, and bytes per `summary` trial. Those
figures set `lab.seconds_per_variant_default`, `lab.sweep_time_budget_minutes`,
`lab.quiet_intervals` and `lab.registry_size_*` (T106's stub, a size-S follow-up). Its
second job is to answer one small question about H1's construction, set out below. Six
variants, all in the `momentum` family's N. The owner accepted that cost on #1295
(2026-10-08).

## Economic rationale for the axes and their values

**The question.** Does H1's result depend on two construction choices: how concentrated the
book is, and how often it is rebalanced to equal weight? H1
([h1-momentum-12-1](../hypotheses/h1-momentum-12-1.md)) fixes the top tenth at a monthly
rebalance. The [handoff](../research/2026-09-24-initial-research-handoff.md) grades 12-1
momentum SUPPORTED (Novy-Marx & Velikov, RFS 2016: a monthly decile portfolio survives
costs). It grades no particular breakpoint or rebalance frequency, so the prior for every
variant is H1's: excess return over SPY centred on zero.

- **`strategy.top_fraction = [0.05, 0.20]`**: half and double H1's 0.10, a book of about
  50 names and one of about 200 from the top-1000 universe. Owner decision on #1295
  (2026-10-08). H1's own value cannot be a variant at `month_end`: that point is H1's
  registered fingerprint, and req 1(b) refuses it. Both values sit on the
  `lab.axis_lattice` step of 0.01. A narrower book is more exposed to momentum's crash
  risk (Daniel & Moskowitz, JFE 2016) and to single names. A wider one moves toward the
  market and should shrink both the excess and the tracking error. The two values differ
  only in `top_fraction`, so each pair shares one read set: a two-variant **read group**
  (spec Definitions). This is the read-group timing the plan asks T114 to measure.
- **`schedule.rebalance_cadence = ["month_end", "week_end", "daily"]`**: every cadence ADR
  0012 admits. The plan line for T114 names all three, so seconds per step are measured at
  each. The signal anchor stays `month_end` (Phase 3's rule), so the ranking changes once a
  month at every cadence. A faster cadence only re-weights the same names back to equal
  weight more often, at a higher cost. The axis asks whether that extra trading earns its
  cost. The prior says it does not: no source in `docs/research/` grades a faster
  rebalance of 12-1 momentum as better net of costs.

2 × 3 = **6 variants** in **3 read groups** (one per cadence), well under
`lab.max_variants_per_sweep` (100). `selection_statistic` is `excess_cagr_spy`.
`dsr_excess` is refused with a cadence axis, because its T differs by cadence. The owner
chose `excess_cagr_spy` on #1295 because it is the quantity H1's prior and H1's retirement
rule are written in.

## Parameters

The fixed values are H1's family rules (the momentum family's first registration, H1). The
keys H1's file pins outside `strategy.*` and `costs.*` (`universe.top_n_by_cap`,
`execution.fill_price`, `alpaca.historical_feed`) are not repeated here. They come from the
family rules on registration, as in the fixture sweeps.

```toml sweep
slug = "momentum-topfrac-cadence"
family = "momentum"
title = "H1 momentum: top fraction x rebalance cadence (first sweep)"
in_sample_start = 2020-08-31

[holdout]
start = 2024-01-01
end = 2026-09-30

[strategy]
formation_months = 12
skip_months = 1
# top_fraction is a grid axis below
weighting = "equal"
signal_total_return = true

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
# rebalance_cadence is a grid axis below
signal_anchor = "month_end"

[grid]
"strategy.top_fraction" = [0.05, 0.20]
"schedule.rebalance_cadence" = ["month_end", "week_end", "daily"]

[lab]
selection_statistic = "excess_cagr_spy"
expected_excess_cagr_spy_pp = 0.0
expected_range_pp = [-1.0, 1.0]
promote_at_least = 0.5
retire_below = -0.01
```

Where each value comes from. The owner decided on #1295 (2026-10-08) that every value is
taken from H1's file or the strategy-lab spec's defaults:
- `in_sample_start`, `[holdout]`, `[strategy]` (except the axis), `[costs]`: H1's
  `toml hypothesis` block, the momentum family rules (req 1(a)).
- `schedule.signal_anchor = "month_end"`: the spec's default (Config keys), H1's rule.
- `expected_excess_cagr_spy_pp = 0.0` and `expected_range_pp = [-1.0, 1.0]`: H1's
  "Expected magnitudes": "Prior centred on **0 pp/yr**; plausible ten-year range **−1 to
  +1 pp/yr**". Both are in percentage points, as the key names say.
- `promote_at_least = 0.5`: the spec's `lab.promotion_min_dsr_excess` default (0.5), the
  lowest the parser accepts. It is also H1's DSR half of its retirement rule ("`dsr_excess`
  below 0.5").
- `retire_below = -0.01`: H1's retirement line, "net excess CAGR over SPY is below −1
  pp/yr". `retire_below` is compared with the selection statistic in the statistic's own
  units (`sweep_report.py`). `excess_cagr_spy` is stored as a fraction (`metrics.py`), so
  −1 pp/yr is written −0.01, not −1.0.

## Expected magnitudes and red flags for the sweep

- **Excess return over SPY (base cost level, 15 bp per side):** every variant's prior is
  H1's, centred on 0 pp/yr in a −1 to +1 pp/yr range. The window is 3.3 years and
  momentum's single-year gaps run ±13 to 18 pp (H1's file), so expect most variants
  **outside** the range. H1's own in-sample trial on the store already sits at −5.8 pp/yr
  (disclosure below). Expect `week_end` and `daily` to trail `month_end` at equal
  `top_fraction` by their extra cost drag: more re-weighting trades at 15 bp per side.
- **Concentration:** `0.05` should show higher tracking error and a wider spread of
  outcomes than `0.20`. The `0.20` variants should sit closer to SPY.
- **Red flags** (a prompt for a look-ahead, cadence-path or cost audit, never a gate):
  - Any variant's base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp`
    (3.0 pp): spec req 15 marks it `red_flag`.
  - `week_end` or `daily` beating `month_end` at the same `top_fraction` by more than the
    whole prior range (2 pp/yr) net of costs. The ranking is identical across cadences
    (month-end anchor), so a large gain from re-weighting alone points at the cadence
    path, not at an edge.
  - `turnover_monthly` at `daily` or `week_end` **below** the `month_end` variant's at
    the same `top_fraction`. More rebalances cannot trade less.
  - The two variants of one read group giving identical results. They hold different
    numbers of names, so identical equity means the group shared targets, not just reads.
  - Any variant with a `kind=holdout` trial. `sweep run` cannot spend the holdout (req 2,
    req 5(a)).

## Power arithmetic per variant count

The momentum family's N before this sweep is **2**: H1's two `ok` in-sample trials (trial 2
carries `n_trials = 2`; H1's holdout trial does not count). After the sweep, with every
variant `ok`, **N = 8**. V comes from the distinct (parameter hash, window) pairs, and
H1's two trials are one pair, so V comes almost entirely from this grid. Its floor is
`lab.min_sharpe_variance_annual` (0.04, a standard deviation of 0.2 in annual Sharpe).
At N = 8 the expected maximum Sharpe of a null grid is about
`√V · ((1 − γ)·Φ⁻¹(1 − 1/8) + γ·Φ⁻¹(1 − 1/(8e)))` ≈ **1.46 · √V**: about **0.29** annual
at the V floor, and more if the grid's real spread is wider.

The excess Sharpe has an 8.4% tracking error (H1's figure) over 40 monthly returns. A
variant needs an annual excess Sharpe at or above SR* for its `dsr_excess` to reach 0.5,
roughly **+2.5 pp/yr** of excess at the V floor. That is two and a half times the top of
the prior range. **The sweep cannot separate an edge inside the prior from selection.** A
promotion at `promote_at_least = 0.5` is reachable only by a result well outside H1's
prior, which would read as a red flag before it read as an edge. The sweep's real product
is the measurement and the answer to the construction question, not a promotion.

## Prior-evidence disclosure

Results already seen before registration:
1. **H1's in-sample trials on the owner's store** (2020-08-31 to 2023-12-29, the same
   window every variant runs). Trial 2 (accepted as T45b's evidence, #503) has base-level
   `excess_cagr_spy` −5.77 pp/yr, `sharpe_annual_excess_spy` −0.27, `turnover_monthly`
   32.4% and `dsr_excess` 0.315 (`psr` basis). Trial 1 is nearly identical (−5.95 pp/yr).
   H1 is the centre point both axes bracket (`top_fraction` 0.10 at `month_end`).
2. **H1's holdout trial** (trial 4, 2023-12-29 to 2026-09-30). Its excess is +4.44 pp/yr
   over SPY at 15 bp. The holdout window is the family's and the sweep never runs on it.
   But its result has been seen, and anyone reading a later promotion of a variant should
   know that.
3. **Everything H1's file discloses** (G1's sources: MTUM, MSCI and AQR live and index
   figures through 2026). See its "Prior-evidence disclosure". None of it grades a top-5%
   or top-20% book or a weekly or daily rebalance.
4. **B3's in-sample trial** (`profitability` family, trial 3) is on the same store and
   window. It is a different family and signal, and is disclosed only because it was seen.

No result for any variant of this grid (`top_fraction` 0.05 or 0.20, or any `week_end` or
`daily` run of 12-1 momentum on this store) has been seen, apart from the scratch-store
timing preview in the PR that adds this file. That preview ran these exact six variants on
a scratch copy of the owner's store to measure seconds per variant. Every value in this
file was fixed before that preview ran, and the copy was deleted.

## Retirement condition

The sweep retires (`tradepartner sweep retire momentum-topfrac-cadence --reason ...`) and
promotes nothing when its argmax, the variant with the highest base-level `excess_cagr_spy`,
is below **−1 pp/yr** (`retire_below = -0.01`). That is H1's own retirement line applied to
the best of six. The sweep also promotes nothing while any variant is `red_flag`ged, until
the audit that flag opens has cleared it. A variant reaches anything beyond this report
only through `sweep promote` and its rules (spec req 4), never through the report alone.

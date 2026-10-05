# Sweep: <title>

<!--
Copy to docs/sweeps/<slug>.md. Merging the file does not register it: the owner runs
`tradepartner sweep register docs/sweeps/<slug>.md` after merge (strategy-lab spec req 1,
docs/specs/strategy-lab.md). A sweep registers one hypothesis per variant of the grid, each
a trial in the family's N whether it wins or loses. The file is frozen once registered: a
changed file is a new registration with new variants, and an unchanged variant of a changed
file is refused as already registered (its fingerprint is taken). Settle the text first.
-->

**Family:** <from `hypotheses.families`>  ·  **Author:**  ·  **Date:** YYYY-MM-DD

## Economic rationale for the axes and their values
<!-- Why each gridded key and each of its values is worth a counted trial, with citations to
primary sources (research reports in docs/research/ and the papers they grade). A grid is a
search over in-sample P&L (charter principle 6): say what question the grid answers and why
these values and no others. -->

## Parameters

The block below is the only part the registry parses. Rules (`backtest/sweep.py`, spec req 1):
- `slug` equals this file's name without `.md`; `family` is one of `hypotheses.families`.
- `in_sample_start` and `[holdout]` must equal the family rules (the family's first
  registration; for `momentum`, H1's). The holdout never comes from live settings.
- The fixed block names **every** `strategy.*` and `costs.*` key and
  `schedule.rebalance_cadence`, each either fixed here or listed as a grid axis, never both.
  Every fixed value except `strategy.*`, `schedule.*` and a **higher** `costs.per_side_bps`
  must equal the family rules. It may pin any other frozen key (`schedule.signal_anchor`,
  `universe.*`, `execution.fill_price`, `backtest.*`, `adjust.*`, `master.*`, `gap.*`,
  `metrics.*`, `benchmarks`, `alpaca.historical_feed`), again equal to the family rules
  where `FORBIDDEN_AXIS_PREFIXES` covers it.
- `[grid]`: dotted key → non-empty list of values, every key in `lab.sweepable_keys`, values
  distinct after `Settings` validation, continuous axes on the family rules' lattice
  (`lab.axis_lattice`, for example `strategy.top_fraction` on a 0.01 step). The variants are
  the Cartesian product, at most `lab.max_variants_per_sweep`; their canonical order is
  ascending `params_sha256`, never the file's order.
- `[lab]`: `selection_statistic` (`dsr_excess`, `sharpe_annual_excess_spy` or
  `excess_cagr_spy`; `dsr_excess` is refused when `schedule.rebalance_cadence` is an axis),
  `expected_excess_cagr_spy_pp`, `expected_range_pp = [lo, hi]`, `promote_at_least` (a
  `dsr_excess` floor ≥ `lab.promotion_min_dsr_excess`) and `retire_below` (a floor on the
  selection statistic; under `dsr_excess` it must be below `promote_at_least`).
- Keep other fenced blocks out of the sweep block; its first bare ``` line closes it.

```toml sweep
slug = "<slug>"
family = "<family>"
title = "<title>"
in_sample_start = YYYY-MM-DD

[holdout]
start = YYYY-MM-DD
end = YYYY-MM-DD

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
"strategy.top_fraction" = [0.05, 0.10]
"schedule.rebalance_cadence" = ["month_end", "week_end"]

[lab]
selection_statistic = "sharpe_annual_excess_spy"
expected_excess_cagr_spy_pp = 0.0
expected_range_pp = [-2.0, 2.0]
promote_at_least = 0.5
retire_below = 0.0
```

Owner answers that set these values (spec open questions), one line each:
-

## Expected magnitudes and red flags for the sweep
<!-- The excess-return prior and range (`expected_excess_cagr_spy_pp`, `expected_range_pp`)
for the whole grid, the share of variants expected inside the range, and the result that
would suggest a bug or look-ahead rather than an edge (`metrics.red_flag_excess_cagr_pp`)
across variants. -->

## Power arithmetic per variant count
<!-- With n variants declared, the family's N after this sweep, today's V and SR* at the
declared count (the report's "SR* at declared count"), what `dsr_excess` the argmax of a null
grid would reach about half the time, and whether `promote_at_least` is reachable by a
plausible edge. Say plainly if the sweep cannot separate an edge from selection. -->

## Prior-evidence disclosure
<!-- Every result already seen for any variant before registration: papers, fund fact sheets,
earlier trials in this family, the research reports' figures (G1's buffer result, for
instance). "None" only if true. -->

## Retirement condition
<!-- In words, beside `retire_below`: the report reading that retires this sweep
(`tradepartner sweep retire --reason`) and promotes nothing, stated before any run. -->

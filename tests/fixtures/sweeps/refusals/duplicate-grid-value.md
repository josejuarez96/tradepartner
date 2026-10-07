# Sweep: Refusal: duplicate-grid-value (test fixture, not a real sweep)

A refusal fixture for `tests/backtest/test_sweep.py`: `strategy.formation_months = [12, 12.0]` validates to one value. Laid out as
[docs/templates/sweep.md](../../../../docs/templates/sweep.md) asks; its numbers are
placeholders, nothing here is a research claim and it is never registered on the real
store.

## Economic rationale for the axes and their values

Placeholder.

## Parameters

```toml sweep
slug = "duplicate-grid-value"
family = "momentum"
title = "Refusal: duplicate-grid-value"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[strategy]
skip_months = 1
top_fraction = 0.20
weighting = "equal"
signal_total_return = true

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
signal_anchor = "month_end"

[gap]
count_share_threshold = 0.05

[grid]
"strategy.formation_months" = [12, 12.0]
"schedule.rebalance_cadence" = ["month_end", "week_end"]

[lab]
selection_statistic = "sharpe_annual_excess_spy"
expected_excess_cagr_spy_pp = 0.0
expected_range_pp = [-2.0, 2.0]
promote_at_least = 0.5
retire_below = 0.0
```

## Expected magnitudes and red flags for the sweep

Placeholder.

## Power arithmetic per variant count

Placeholder.

## Prior-evidence disclosure

Placeholder: none seen, it is a fixture.

## Retirement condition

Placeholder.

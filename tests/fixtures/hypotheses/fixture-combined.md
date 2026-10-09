# Hypothesis: fixture combined (test fixture, not a real hypothesis)

B4's twin on the fixture store, for `tests/backtest/` (plan T130), laid out as
[docs/templates/hypothesis.md](../../../docs/templates/hypothesis.md) asks. Its numbers
are placeholders: nothing here is a research claim and it is never registered on the real
store. `combined` composes momentum and profitability, so this file names every
`combined.*`, `strategy.*`, `profitability.*` and `costs.*` key: a B4 registration
freezes, requires and hashes both sub-signals' keys and reads neither live.

## Economic rationale

Placeholder. A real file cites its primary sources here (for B4: the hypothesis backlog's
G4-2 and HO-10).

## Parameters

The block below names every required key of a `combined` file (`in_sample_start`,
`holdout.start`, `holdout.end`, every `combined.*`, `strategy.*`, `profitability.*` and
`costs.*` key). `top_fraction = 0.2`: about 15 scored fixture issuers give a book of
about three names, so one name crossing the cut changes it. The two sub-signal sections
are the momentum and profitability twins' values, placeholder for both.

```toml hypothesis
slug = "fixture-combined"
family = "combined"
title = "Fixture equal-rank momentum plus gross profitability"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[combined]
top_fraction = 0.2
weighting = "equal"

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
weighting = "equal"
signal_total_return = true

[profitability]
basis = "gross"
annual_period_days = [350, 380]
max_fact_age_days = 548
exclude_sic_ranges = [[6000, 6999]]
include_derived = true
top_fraction = 0.10
weighting = "equal"

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[gap]
count_share_threshold = 0.05
```

## Expected magnitudes and red flags

Placeholder.

## Power arithmetic

Placeholder.

## Prior-evidence disclosure

Placeholder: none seen, it is a fixture.

## Retirement condition

Placeholder.

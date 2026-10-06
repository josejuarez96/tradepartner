# Hypothesis: fixture profitability (test fixture, not a real hypothesis)

B3's twin on the fixture store, for `tests/backtest/` (plan T85), laid out as
[docs/templates/hypothesis.md](../../../docs/templates/hypothesis.md) asks. Its numbers
are placeholders: nothing here is a research claim and it is never registered on the
real store. It shares every non-signal key with `fixture-momentum.md`, so the two files
differ only in `family` and the signal section.

## Economic rationale

Placeholder. A real file cites its primary sources here (for B3: Novy-Marx 2013).

## Parameters

The block below names every required key of a `profitability` file (`in_sample_start`,
`holdout.start`, `holdout.end`, every `profitability.*` and `costs.*` key) and no
`strategy.*` key. `top_fraction = 0.2`: about 15 scored fixture issuers give a book of
about three names, so one name crossing the cut changes it.

```toml hypothesis
slug = "fixture-profitability"
family = "profitability"
title = "Fixture gross profitability"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[profitability]
basis = "gross"
annual_period_days = [350, 380]
max_fact_age_days = 548
exclude_sic_ranges = [[6000, 6999]]
include_derived = true
top_fraction = 0.2
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

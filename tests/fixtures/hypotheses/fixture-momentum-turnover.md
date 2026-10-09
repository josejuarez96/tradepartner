# Hypothesis: fixture momentum with the turnover screen (test fixture, not a real hypothesis)

The momentum fixture twin with B10's share-turnover screen on (backtest spec amendment
#1358, T165c), for the look-ahead suites' screened case. A complete hypothesis file,
laid out as [docs/templates/hypothesis.md](../../../docs/templates/hypothesis.md) asks.
Its numbers are placeholders: nothing here is a research claim and it is never
registered on the real store.

## Economic rationale

Placeholder. A real file cites its primary sources here (for H1: handoff H1 and G1).

## Parameters

The block below is the only part the registry reads. It is the momentum twin's block at
`formation_months = 1`, `skip_months = 0`, `turnover_top_fraction = 0.5` and
`top_fraction = 0.5`, so the fixture universe's few names still leave several targets.

```toml hypothesis
slug = "fixture-momentum-turnover"
family = "momentum"
title = "Fixture 1-0 momentum, turnover-screened"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[strategy]
formation_months = 1
skip_months = 0
top_fraction = 0.5
turnover_top_fraction = 0.5
weighting = "equal"
signal_total_return = true

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

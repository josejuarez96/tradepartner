# Hypothesis: fixture momentum (test fixture, not a real hypothesis)

A complete hypothesis file for `tests/backtest/test_hypothesis.py`, laid out as
[docs/templates/hypothesis.md](../../../docs/templates/hypothesis.md) asks. Its numbers
are placeholders: nothing here is a research claim and it is never registered on the
real store.

## Economic rationale

Placeholder. A real file cites its primary sources here (for H1: handoff H1 and G1).

## Parameters

The block below is the only part the registry reads. It names every required key
(`in_sample_start`, `holdout.start`, `holdout.end`, every `strategy.*` and `costs.*`
key) and pins `gap.count_share_threshold` as an example of an optional frozen key.

```toml hypothesis
slug = "fixture-momentum"
family = "momentum"
title = "Fixture 12-1 momentum"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
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

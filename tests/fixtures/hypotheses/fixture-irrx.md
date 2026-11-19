# Hypothesis: fixture irrx (test fixture, not a real hypothesis)

The `irrx` family's twin on the fixture store, for `tests/backtest/` (plan T414), laid out
as [docs/templates/hypothesis.md](../../../docs/templates/hypothesis.md) asks. Its numbers
are placeholders: nothing here is a research claim and it is never registered on the
real store. Its values are the family's frozen defaults (backtest spec amendment
2026-11-16, #3504).

## Economic rationale

Placeholder. A real file cites its primary sources here (for `irrx`: Dai, Medhat,
Novy-Marx and Rizova, FAJ 2024).

## Parameters

The block below names every required key of an `irrx` file (`in_sample_start`,
`holdout.start`, `holdout.end`, every `irrx.*`, `costs.*` and `portfolio.*` key) and no
`strategy.*` or `profitability.*` key. It is a `long_short`, `month_end`, `open`-fill
book, with the store's risk-free series, as item 7 to 9 of the amendment fix.

```toml hypothesis
slug = "fixture-irrx"
family = "irrx"
title = "Fixture announcement-adjusted reversal"
in_sample_start = 2017-01-31

[holdout]
start = 2023-01-03
end = 2025-12-31

[irrx]
lookback_sessions = 21
min_kept_sessions = 15
announce_post_sessions = 2
event_form = "8-K"
event_item = "2.02"
event_coverage_sessions = 126
dissemination_cutoff = "17:30"
industry_digits = 2
min_industry_names = 5
min_dollar_volume = 200000000.0
adv_sessions = 63
min_adv_bars = 42
top_fraction = 0.20
weighting = "equal"
signal_total_return = true

[portfolio]
side = "long_short"
gross_exposure = 1.0
net_exposure = 0.0
short_min_price = 5.0
short_cover_weight = 0.10

[costs]
per_side_bps = 50.0
spread_bps = 0.0
impact_coeff = 0.0
commission_per_share = 0.0
commission_per_order = 0.0
adv_sessions = 20
sensitivity_per_side_bps = [8.8, 25.0, 60.0]
borrow_bps_per_year = 30.0
borrow_sensitivity_bps = [0.0, 300.0]

[schedule]
rebalance_cadence = "month_end"
signal_anchor = "month_end"

[execution]
fill_price = "open"

[metrics]
risk_free_source = "store"

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

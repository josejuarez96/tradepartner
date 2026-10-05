# Experiment: E1-H: D1 demand deterioration predicts next-quarter revenue-growth deceleration (human labels)

Fixture reproduction of docs/specs/research-registry.md's first Data / interfaces example.

```toml experiment
slug = "e1h-demand-deterioration-revenue"
kind = "economic"
stage = 5
title = "E1-H: D1 demand deterioration predicts next-quarter revenue-growth deceleration (human labels)"
confirmatory = true
provenance = "human"
touches_returns = false
claims = ["ER-4", "ER-5", "INT-4"]
hypothesis_ref = "B5"
seed = 20261003

[dataset]
name = "e1h-labels-revenue-panel"

[window]
start = 2017-01-01
end = 2023-12-31

splits = ["pilot"]

[primary]
metric = "coef_deteriorated"
direction = "less"
threshold = 0.0
ci_level = 0.95
min_clusters = 30
inference = "two-way clustered SE (firm, calendar quarter), one-sided 95% upper bound"
secondary = ["p_one_sided", "mse_oos_C", "mse_oos_B", "n_firms", "n_quarters"]
comparison_set = "A fundamentals; B = A + deterministic text; C = B + D1 indicators; identical samples and splits"

[multiplicity]
method = "none"

[budget]
runs = 3
configurations = 1
stop_rule = "confirmatory only on the hindsight-free subset (both annotators answered no); the full sample is a sensitivity analysis; a coefficient that flips sign when sector fixed effects are dropped is reported as unstable"
expected_effect = "weak negative relation, partly absorbed once contemporaneous growth is controlled (B5 prior)"
```

# Experiment fixture: primary.min_clusters = 0

Deliberately invalid fixture for tests/research/test_experiment.py (primary.min_clusters = 0).

```toml experiment
slug = "refuse-min-clusters-zero"
kind = "benchmark"
stage = 3
title = "Fixture: min_clusters = 0"
confirmatory = false
provenance = "deterministic"
touches_returns = false
claims = ["FX-1"]
seed = 1

[dataset]
name = "fx-dataset"

[window]
start = 2020-01-01
end = 2020-12-31

splits = ["dev"]

[primary]
metric = "m"
direction = "greater"
ci_level = 0.95
min_clusters = 0
inference = "x"
secondary = []
comparison_set = "x"

[multiplicity]
method = "none"

[budget]
runs = 3
configurations = 1
stop_rule = "x"
expected_effect = "x"
```

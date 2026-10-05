# Experiment fixture: family outside hypotheses.families

Deliberately invalid fixture for tests/research/test_experiment.py (family outside hypotheses.families).

```toml experiment
slug = "refuse-family-unknown"
kind = "benchmark"
stage = 3
title = "Fixture: family outside hypotheses.families"
confirmatory = false
provenance = "deterministic"
touches_returns = true
claims = ["FX-1"]
seed = 1
family = "not_a_real_family"

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
min_clusters = 5
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

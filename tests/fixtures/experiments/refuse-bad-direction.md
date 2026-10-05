# Experiment fixture: primary.direction outside the DIRECTIONS set

Deliberately invalid fixture for tests/research/test_experiment.py (primary.direction outside the DIRECTIONS set).

```toml experiment
slug = "refuse-bad-direction"
kind = "benchmark"
stage = 3
title = "Fixture: bad direction"
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
direction = "not_a_direction"
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

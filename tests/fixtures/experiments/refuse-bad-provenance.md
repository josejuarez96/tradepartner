# Experiment fixture: provenance outside the PROVENANCES set

Deliberately invalid fixture for tests/research/test_experiment.py (provenance outside the PROVENANCES set).

```toml experiment
slug = "refuse-bad-provenance"
kind = "benchmark"
stage = 3
title = "Fixture: bad provenance"
confirmatory = false
provenance = "not_a_provenance"
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

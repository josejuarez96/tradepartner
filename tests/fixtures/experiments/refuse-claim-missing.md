# Experiment fixture: a claim id absent from the fixture claims.toml

Deliberately invalid fixture for tests/research/test_experiment.py (a claim id absent from the fixture claims.toml).

```toml experiment
slug = "refuse-claim-missing"
kind = "benchmark"
stage = 3
title = "Fixture: claim id not in claims.toml"
confirmatory = false
provenance = "deterministic"
touches_returns = false
claims = ["NOT-A-REAL-CLAIM"]
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

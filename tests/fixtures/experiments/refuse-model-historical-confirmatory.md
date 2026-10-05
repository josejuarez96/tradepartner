# Experiment fixture: confirmatory = true with provenance = "model_historical"

Deliberately invalid fixture for tests/research/test_experiment.py (confirmatory = true with provenance = "model_historical").

```toml experiment
slug = "refuse-model-historical-confirmatory"
kind = "benchmark"
stage = 3
title = "Fixture: model_historical confirmatory"
confirmatory = true
provenance = "model_historical"
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

# Experiment fixture: an UNGRADED claim backing a confirmatory registration

Deliberately invalid fixture for tests/research/test_experiment.py (an UNGRADED claim backing a confirmatory registration).

```toml experiment
slug = "refuse-claim-ungraded-confirmatory"
kind = "benchmark"
stage = 3
title = "Fixture: UNGRADED claim, confirmatory"
confirmatory = true
provenance = "deterministic"
touches_returns = false
claims = ["FX-UNGRADED"]
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

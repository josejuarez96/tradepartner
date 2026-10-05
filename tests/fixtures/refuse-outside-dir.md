# Experiment fixture: file outside research.experiments_dir

Deliberately placed outside tests/fixtures/experiments/ (the fixture
`research.experiments_dir`) for tests/research/test_experiment.py.

```toml experiment
slug = "refuse-outside-dir"
kind = "benchmark"
stage = 3
title = "Fixture: file outside experiments_dir"
confirmatory = false
provenance = "deterministic"
touches_returns = false
claims = ["FX-1"]
seed = 1
splits = ["dev"]

[dataset]
name = "fx-dataset"

[window]
start = 2020-01-01
end = 2020-12-31

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

# Experiment: Annotation pilot: agreement on D1 to D3 in both modes (protocol §8)

Fixture reproduction of docs/specs/research-registry.md's second Data / interfaces example.

```toml experiment
slug = "pilot-agreement-d1-d3"
kind = "agreement"
stage = 2
title = "Annotation pilot: agreement on D1 to D3 in both modes (protocol §8)"
confirmatory = true
provenance = "human"
touches_returns = false
claims = ["TX-1", "AP-1"]
hypothesis_ref = "B8"
seed = 20261003

[dataset]
name = "annotation-pilot-labels"

[window]
start = 2017-01-01
end = 2023-12-31

splits = ["pilot"]

[primary]
metric = "alpha_J_min_over_dimension_mode"
direction = "greater"
threshold = 0.667
ci_level = 0.95
min_clusters = 100
inference = "issuer-cluster bootstrap, 2000 resamples, percentile intervals, lower bound"
secondary = ["alpha_J_by_cell", "alpha_S_by_cell", "specific_agreement_V_RuC", "specific_agreement_V_R", "n_adverse_votes", "extraction_failure_rate", "seconds_per_pair_p50", "seconds_per_pair_p90"]
comparison_set = "annotators A, B on all pairs; C on the 25% subset"

[multiplicity]
method = "none"

[budget]
runs = 6
configurations = 1
stop_rule = "G1 or G3 or G4 fails: revise and re-test on fresh pairs, at most two rounds, only the final fresh round decides; G4 under 20 adverse votes is underpowered, one extension batch of up to 50 pairs"
expected_effect = "tentative agreement (0.667 to 0.800) on D1 and D3 cross_document; D2 uncertain"
```

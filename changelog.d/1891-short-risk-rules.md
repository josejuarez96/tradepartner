- T231: the short risk rules in check_phase and three RiskConfig keys; short refusal lifted only for long_short on a paper broker (#1900)
### Added
- execution.risk: check_phase takes window_side, broker_paper, net_exposure; rules max_short_weight, net_exposure_band, min_short_equity, cover_within_holding, one_side_per_name; gross is sum of abs value; the cash rule deducts held shorts
- config: risk.max_short_weight, net_exposure_band, min_short_equity (defaults 0.05, 0.10, 2000), read from windows frozen before T231 at their defaults

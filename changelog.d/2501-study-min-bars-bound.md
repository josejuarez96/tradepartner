- Reject min_volume_bars > volume_lookback and beta_min_bars > beta_lookback in study parser
### Fixed
- Added validation in _validate_study to refuse min_volume_bars > volume_lookback and beta_min_bars > beta_lookback; added tests in test_experiment_study.py

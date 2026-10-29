- Study parser: min bars bounded by lookback; z checked against the declared one- or two-sided interval
### Fixed
- Study parser refuses min_volume_bars above volume_lookback and beta_min_bars above beta_lookback, and checks study.z against the interval side primary.inference declares (one-sided 1.645, two-sided 1.96 at 95%) (#2501)

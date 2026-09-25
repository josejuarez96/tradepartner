### Added
- Phase 3 T32: `tradepartner.backtest.metrics`: `series_metrics` (the 17 req 7 keys per series and cost level, `*_excess_spy` null for SPY, constant series refused), `expected_max_sharpe`, `probabilistic_sharpe`, `deflated_sharpe` per basis (`psr` with fewer than two pairs), `red_flag`; `MONTHS_PER_YEAR = 12`, gamma from `numpy.euler_gamma` (#127).

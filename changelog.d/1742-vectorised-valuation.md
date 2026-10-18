- T207 (#1742): value_positions and carry_to_fill run over polars frames (byte-identical to the per-name loop, pinned on the standard fixture); 1,000-name 10-year daily scale harness added (slow, RUN_SLOW_TESTS=1).
### Added
- tests/backtest/test_engine_scale.py: the synthetic 1,000-name, 10-year daily scale fixture and the BACKTEST_SCALE_BUDGET_SECONDS test (slow) (T207).
### Changed
- backtest: value_positions and carry_to_fill are vectorised over polars frames; equity, rebalance rows and metrics are byte-identical to the per-name loop (T207).

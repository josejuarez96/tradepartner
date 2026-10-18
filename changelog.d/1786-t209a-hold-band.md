- #1786 T209a: hold-mode membership and the no-trade band as pure functions, with the four strategy keys and `cash_rate = store` (PR #1788).
### Added
- Backtest: `hold_membership` and `apply_rebalance_band` pure functions in `backtest/portfolio.py`, shared with the paper planner (T205) (#1786).
### Changed
- Backtest config: `strategy.weighting` gains `hold`, and `strategy.hold_sessions`, `strategy.hold_target_names` and `strategy.rebalance_band` are added; `backtest.cash_rate` accepts `store` (#1786).
- Backtest config: until T209b wires the engine, `weighting = hold` and a non-zero `rebalance_band` are refused by a config validator, naming T209b (#1786).

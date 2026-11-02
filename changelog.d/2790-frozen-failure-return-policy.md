task: T328a (PR #2815)
- T328a (first slice): frozen backtest.delisting_failure_return key (none/failure_v1/severe_v1), short refusal, not yet applied by the engine; pinned hashes unchanged.
### Added
- Frozen backtest key `backtest.delisting_failure_return` with versioned delisting failure-return policies (`none`, `failure_v1`, `severe_v1`); a nonzero policy is refused on a short book and cannot run until the engine applies it (#2790).

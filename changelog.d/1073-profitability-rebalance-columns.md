- #1073 trial_rebalances gains the six profitability rebalance counts (schema v13, T85d)
### Added
- Store: `trial_rebalances` gains the six `profitability`-family counts (`n_ranked`, `n_excluded_*`, `n_derived`); schema version 13; `RebalanceRow` takes them as optional, NULL when absent (#720, #1033, T85d)

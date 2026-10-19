- T209b: the engine runs hold mode (exit on session N, remainder in cash), applies the no-trade band to targets and accrues cash from the store's T-bill series; hold and a band now load (#1839)
### Added
- Backtest engine: strategy.weighting = hold, strategy.rebalance_band and backtest.cash_rate = store are wired (exits on session N, band on targets, per-session cash accrual); the T209a guard is removed.

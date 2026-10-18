- T170c (PR pending): the engine reads the risk-free series once at the cutoff under store, metric_rows builds rf_i from it, risk_free_missing fails before any row; constant unchanged.
### Added
- backtest: with metrics.risk_free_source = store the engine reads rates once at the data cutoff and metric_rows compounds the T-bill accrual per period; a session with no rate known fails the trial risk_free_missing before any result row

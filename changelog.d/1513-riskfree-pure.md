- T170 risk-free pure module (backtest/riskfree.py) and series_metrics taking a per-period risk-free sequence; risk_free_annual joins METRIC_KEYS; H1 metric rows pinned byte-identical; engine/store wiring is T170b/c.
### Added
- backtest: riskfree.py (to_yield, session_accruals, period_risk_free, constant_period_returns) and series_metrics taking a per-period risk-free sequence; new risk_free_annual metric (ADR 0020, T170)

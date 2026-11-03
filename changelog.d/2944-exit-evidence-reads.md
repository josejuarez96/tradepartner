task: T328b (PR #2947)
- T328b: exit-evidence provider reads (delisting reasons, merger exits with raw acquirer close) at the data cutoff (#2947)
### Added
- Backtest provider reads `delisting_reasons` and `merger_exits`: exit evidence known at the run's data cutoff, the acquirer's raw close on the merger's effective date (T328b, #2944).

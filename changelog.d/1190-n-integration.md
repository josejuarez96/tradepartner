- T83b (#1190): the backtest's N adds the family's research runs (family_run_count) and stores n_research; V unchanged; the backtest page shows today's N split (backtest trials, research runs); backtest spec req 8 amended.
### Added
- Backtest N counts research runs on return data: `n_trials` = counted backtest trials + `family_run_count`, the research share stored in `trial_results.n_research`; the backtest page shows today's N split (#1190, T83b).

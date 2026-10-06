- T98 (#1072, PR #1075): engine, holdout and run path at cadence: engine.run at the frozen rebalance_cadence, signal frame read from A_form, holdout window and gap gate at cadence; H1 at month_end byte-identical.
### Added
- Backtest engine, holdout decisions and run path at the frozen `schedule.rebalance_cadence` (`month_end`, `week_end`, `daily`), with the signal at the frozen anchor read from the formation anchor on (strategy-lab T98).

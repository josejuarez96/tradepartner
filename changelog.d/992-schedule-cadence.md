- T94 (#992): rebalance_sessions/fill_session/read_time take a cadence (month_end, week_end, daily); calendar gains last_session_of_week and rebalance_sessions_between; PERIODS_PER_YEAR in backtest/schedule.py.
### Added
- Rebalance schedule at a cadence: `month_end` (unchanged), `week_end` (last XNYS session of each ISO week) and `daily`, from the calendar's sessions, with `PERIODS_PER_YEAR` (T94, #992).

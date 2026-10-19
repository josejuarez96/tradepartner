- T217 built: strategy.signal_delay_sessions, placebo and placebo_seed behind a T217c guard, three FROZEN_KEY_DEFAULTS entries at version 29, and the pure placebo.py (shuffle, random_entry, shock_note); the engine reads them in T217c
### Added
- Kill ladder rung-4 and rung-5 keys (`strategy.signal_delay_sessions`, `strategy.placebo`, `strategy.placebo_seed`), guarded until T217c, and the pure `backtest/placebo.py` (T217, #1824)

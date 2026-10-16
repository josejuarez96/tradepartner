- #1712 Spin-off writer stamps known_at at health's ex-date floor
### Fixed
- Data: the spin-off writer raises a stored `known_at` to `session_close(previous_session(ex_date))` (one shared helper `adapters.prices.spinoff_known_at_floor`), the floor `health --check`'s `spinoff_known_at_floor` rule enforces, so writer and check cannot disagree (#1712).

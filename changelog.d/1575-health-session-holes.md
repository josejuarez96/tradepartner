- health --check fails on a session hole: an XNYS session in the universe.min_history_months window whose prices_daily rows are below health.min_session_rows_share of the previous session's (#1575, #1550 part c)
### Added
- health: the session_holes integrity rule names each XNYS session in the universe history window with no bars, or below health.min_session_rows_share of the previous session's rows, with its row count and the previous session's; health --check fails on it. From #1575 (#1550 part c).

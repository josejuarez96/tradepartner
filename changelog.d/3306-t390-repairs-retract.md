task: T390 (PR #3310)
- T390: repair-resolution, repair-bars, repair-dividends and repair-spinoff-splits retract into retracted_rows (reason, release, rule) instead of deleting
### Added
- The four row-changing repairs now move rows to retracted_rows instead of deleting; their summaries say retract (T390, #3275).

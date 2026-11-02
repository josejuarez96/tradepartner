- Form 4 bulk finds 2026q2 onward at SEC's new datastandardsinnovation path; per-quarter sec_insider_bulk rows carry their own remaining and status; health sums one run's rows (#2728).
### Changed
- Per-quarter `sec_insider_bulk` run rows record `quarter=`, that quarter's own `remaining=` (status `ok` at 0) and the run-wide `run_remaining=`; health's latest line sums rows that share one start time instead of an arbitrary one (#2728).
### Fixed
- Form 4 bulk: a quarter SEC publishes only under `/files/datastandardsinnovation/` (2026q2 onward) was a 404 and counted as `sec_forms_bulk_quarter_missing`; the zip and HEAD now try both paths (#2728).

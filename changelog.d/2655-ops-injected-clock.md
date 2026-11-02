- #2655 (PR #2656): paper status and the ops readers take the caller's clock, not the wall clock; fixes the --all test red since the 2026-10-30 close.
### Fixed
- `paper status` reads staleness, EDGAR age and the next rebalance at the command's clock instead of the wall clock (#2655).

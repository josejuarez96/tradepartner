- T156 (#1398): paper status/report/check take --book (status --all prints one line per book); the operations page has a per-book summary and a book selector; the override page writes to the selected book's window.
### Added
- `paper status --book <token>` and `--all`, `paper report --book` and `paper check --book`; the operations page's one-row-per-book summary and book selector; the override page's book selector (ADR 0017 B.7, T156).
### Changed
- The tracking report's names are per rebalance period (`PeriodComparison`, `compare_periods`, `_trial_periods`); `paper check`'s tracking line names the period of the window's cadence (`week`, `session`), `month` at `month_end` as before.

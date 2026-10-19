- T210b done: backtest --measured-cost-bps adds one labelled measured cost level (ADR 0023 kill-ladder rung 1); trial_metrics.cost_level_kind (schema 33), in-sample only.
### Added
- backtest --measured-cost-bps <x> runs one more in-sample cost level, stored as trial_metrics.cost_level_kind = 'measured' (never the base, never in N, V or DSR); it needs --note naming the source book(s) and window, and a holdout or tracking run, or an x equal to the base or a ladder level, is refused (refused_measured_level) before any trial row (#1792).
### Changed
- Schema version 33: trial_metrics gains the NOT NULL cost_level_kind column (base, ladder or measured), rebuilt by _migrate_cost_level_kind with every row kept and backfilled base at the trial's stored base level and ladder elsewhere; the backtest page's per-level column headers carry the kind (#1792).

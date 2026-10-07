- T127 (#1153): schema version 14, generic per-rebalance counts table (B3 trial's counts migrated), signals.reason prefix CHECK; Plan.exclusions. Owner's store migrates at its next writing job.
### Added
- Schema version 14: `trial_rebalance_counts` (one row per count per rebalance, existing counts migrated), `Plan.exclusions`, and `signals.reason` accepting any `excluded_<reason>` (ADR 0014, T127).

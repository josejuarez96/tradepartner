- #647 items 5 and 7 (PR #707): full exits net their own open sells like trims; holding-capped trims are held, not skipped (no skip-cap trips after a broad drop). Items 1-4, 6, 8, 9 open.
### Fixed
- A full exit of a name with its own open sells no longer halts the batch on `sell_sum_within_holding`; a trim the holding cap already limited is held and retried instead of closing as `skip_below_minimum` and counting toward `risk.skip_cap` (#647).

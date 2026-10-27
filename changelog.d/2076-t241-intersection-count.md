task: T241 (PR #2105)
- Store: schema version 38 records the liquidity intersection on the rebalance row (#2105).
### Changed
- Schema version 38: `trial_rebalances` and `paper_plans` gain `n_assets_intersected` and `assets_intersection_skipped` (NULL on existing rows), and the engine copies the read universe's intersection record onto every rebalance row (plan T241, #2076).

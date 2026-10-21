- Store: schema version 36 records the liquidity intersection on the rebalance row; held until 2026-11-05 (#2105).
### Changed
- Schema version 36: `trial_rebalances` and `paper_plans` gain `n_assets_intersected` and `assets_intersection_skipped` (NULL on existing rows), and the engine copies the read universe's intersection record onto every rebalance row (plan T241, #2076).

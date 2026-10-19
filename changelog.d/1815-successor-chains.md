- Successor chains: the config lists, the successor_chains table, the rows at the evidence's known_at and the run-row counts (#821, T187).
### Added
- Added `master.chains`, `master.chains_dismissed`, `master.chain_window_sessions` and `master.chain_max_price_ratio` (#821).
- Added the `successor_chains` table (schema version 31) and the EDGAR master pass that derives, writes, retracts and rewrites a chain row at the evidence's `known_at` (#821).

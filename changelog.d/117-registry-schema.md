### Added
- Phase 3 T31: trial-registry tables (`hypotheses`, `trials`, `trial_results`, `trial_metrics`, `trial_rebalances`, `trial_equity`, `trial_weights`, `owner_decisions`) in `store/schema.py` at schema version 2, with `RegistryNotInitialised` for read-only connections to a version-1 store (#117).
### Changed
- `store.schema.init_schema` migrates a version-1 store to version 2 by creating the registry tables and appending a version row; fact tables are unchanged, and a read-only connection runs no DDL (#117).

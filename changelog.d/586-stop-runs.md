- T63f stop runs: a stop run misses the pending rebalance, re-attempts open forced exits and sells each held name down to its residue through the wrapper; outcomes take the stop horizons
### Added
- `paper run` of kind `stop` (T63f): `window_stop` exits for the holding minus its residue, the pending rebalance missed with reason `window_stop`, and the stopped window's outcome horizons

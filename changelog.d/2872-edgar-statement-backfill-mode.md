- EDGAR adapter statement-backfill mode (#2872, #2837, PR #2887): statement_facts reads local caches only; missing stamps skipped and counted; zip-absent empty results carried forward
### Fixed
- EDGAR adapter: a statement-backfill mode (`statement_backfill=True`) in which statement facts read only local caches: FSN from cached manifests, a missing stamp skipped and counted, and a zip-absent CIK's cached empty result carried forward with no request (#2872, #2837)

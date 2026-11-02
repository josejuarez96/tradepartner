task: T323 (PR #2834)
- T323 incremental EDGAR ingest driver and --rebuild-index (PR #2834), behind edgar.incremental_index (default off)
### Added
- Incremental EDGAR ingest behind `edgar.incremental_index`, and `ingest --source edgar --rebuild-index` to seed or repair the filing index one quarter per chunk (T323, #2824).

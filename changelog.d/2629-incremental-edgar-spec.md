- Spec amendment (#2629): the nightly EDGAR ingest becomes incremental: stamped filing index in a store table, daily-index file cursor, touched-CIK rebuilds, settled-quarter reconcile, full walk only under --rebuild-index.
### Changed
- Data-foundation spec: amendment 2026-10-30 (#2629) makes the nightly EDGAR ingest incremental (persisted filing_index table, daily-index cursor, per-CIK rebuilds, reconcile, rebuild flag), targeting 20 min and 1.5 GB peak.

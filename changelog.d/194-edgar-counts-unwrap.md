### Fixed
- `ingest._source_counts` unwraps the `_Recorded` proxy before reading the adapter's counts, restoring the `unstamped:` / `skipped filers:` part of the EDGAR run message (#194).

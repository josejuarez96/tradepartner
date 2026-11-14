- health --check fails an empty or stale store (store_stale, health.max_stale_sessions); the daily ingest counts a thin recent session as missed (PR #3373)
### Fixed
- health --check exits non-zero on an empty store or one more than health.max_stale_sessions behind; the daily ingest treats a recent session without the reference bar or over ingest.max_missing_share as missed (#3329)

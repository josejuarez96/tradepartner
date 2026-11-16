- raw-archive migrate-cache records mismatched / evict_failed / status_failed as retryable states (not clean moves); summary counts them, a mismatch exits 1
### Added
- migrate-cache no longer reports a corrupt archived body or evict error as a clean move; they are retried next run

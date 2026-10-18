- #1757: paper run retries read-only store opens for store.lock_retry_seconds; a lock before any submit ends stale-style (no switch).
### Fixed
- paper run: read-only store opens retry for store.lock_retry_seconds; a store still locked before any order ends the run stale-style (stale_data alert, no kill switch), after submits began it still fails closed (#1757)

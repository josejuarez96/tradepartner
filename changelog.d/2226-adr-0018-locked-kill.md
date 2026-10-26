### Changed
- ADR 0018 gains a dated amendment: a kill on a locked store waits up to store.writer_lock_retry_seconds, then exits 3 with NOT engaged, not 5/409 (#2226).

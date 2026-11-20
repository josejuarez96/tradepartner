- #3640: the paper run lock is retried for paper.run_lock_retry_seconds (0.25 s), so a reader's lock probe no longer costs the scheduled run its session.
### Fixed
- `paper run` (and every command that takes the run lock) retries a refused lock every `paper.run_lock_poll_seconds` for `paper.run_lock_retry_seconds` (default 0.25 s) before exiting `locked`: a dashboard or `paper status` probe holding the shared lock for an instant no longer makes the run miss its session; 0 restores refuse-at-once (#3640).

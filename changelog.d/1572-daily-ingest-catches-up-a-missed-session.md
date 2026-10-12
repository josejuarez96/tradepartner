### Fixed
- The daily alpaca ingest catches up the XNYS sessions its store lacks before the run's expected session (up to `alpaca.max_catchup_sessions`, default 5), each session's bars stamped `bar_known_at`; a longer lapse fails the run and names the sessions and the `--backfill --since` command (#1572, from #1550)

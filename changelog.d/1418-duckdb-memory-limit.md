- #1418: optional STORE__MEMORY_LIMIT / STORE__THREADS cap DuckDB on every store connection (OOM guard for long sweeps); unset keeps DuckDB's default.
### Added
- Optional `store.memory_limit` and `store.threads` config keys cap DuckDB's memory and worker threads on every store connection; unset keeps DuckDB's defaults (#1418).

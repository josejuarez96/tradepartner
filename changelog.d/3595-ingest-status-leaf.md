- Order wrapper reads ingest run-status constants from a stdlib-only leaf, so its import closure excludes ingest (#3595).
### Changed
- The order wrapper and ops reader import ingest's run-status constants from tradepartner.ingest_status, not the 8.7k-line ingest module (#3595).

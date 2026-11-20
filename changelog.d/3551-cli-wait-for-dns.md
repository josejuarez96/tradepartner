- Scheduled ingest waits (bounded backoff) for DNS before its sources start (#3551)
### Added
- Scheduled `tradepartner ingest` waits up to `ingest.network_wait_seconds` for the source hosts to resolve, then runs as before (#3551)

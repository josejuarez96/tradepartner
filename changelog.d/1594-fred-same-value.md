- FRED ingest compares each returned rate with the value in effect at its stamp and keeps the later vintage on collisions.
### Fixed
- Prevent duplicate rate revision rows across daily re-fetches and retain the later vintage on a shared stamp.

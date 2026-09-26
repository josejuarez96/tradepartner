### Changed
- `ingest_session`: the daily price chunk's fetch pass (`_fetch_prices`) runs before `open_for_write`; the write transaction only writes (#173).

- T348 (#2755): Form 4 XML and new insider zips are kept by hash before parse, each Form 4 writer run writes a provenance manifest its run rows cite, and a changed zip rechecks held XML once; archive off runs as before.
### Added
- Form 4 raw retention (T348, #2755): with `TRADEPARTNER_RAW_ARCHIVE` set, fetched Form 4 XML and each new insider bulk zip are archived by SHA-256 before parsing; the `edgar`/`sec_forms` writer run archives one provenance manifest (row key, raw hash, URL, UTC fetch time, parser version, written rows' key and pinned digest) and its message and quarter rows gain `provenance_sha256`; a changed bulk zip rechecks held XML accessions once (rule 4).
### Changed
- `sec_insider_bulk` quarter messages now lead with `checked_sha256`, then `provenance_sha256` (archive on), then `zip_sha256`, and are cut by `ingest.max_message_chars` like other run messages (T348).

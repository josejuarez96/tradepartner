- EDGAR filename fetch stops and fails the run closed after edgar.filename_fetch_outage_ciks (default 20) CIKs in a row fail past the retries, instead of retrying every remaining CIK in a full SEC outage (#2934).
### Added
- edgar.filename_fetch_outage_ciks (default 20): the filename fetch pass stops, failing the run closed, once that many CIKs in a row fail past the retries (#2934).

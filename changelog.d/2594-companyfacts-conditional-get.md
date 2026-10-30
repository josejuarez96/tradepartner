- #2594: nightly submissions.zip / companyfacts.zip fetched with a conditional GET; a 304 keeps the cached zip (PR #2607)
### Fixed
- The nightly EDGAR ingest no longer re-downloads the 1.4 GB companyfacts.zip (or submissions.zip) when SEC has not changed it: a conditional GET keeps the cached copy on a 304 (#2594).

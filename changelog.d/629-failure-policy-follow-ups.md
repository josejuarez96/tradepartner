- EDGAR failure-policy follow-ups: `_prefetch` needs an explicit `dry_run`; a NaN/inf value withholds one key, not the source; one shared message cleaner ([#629](https://github.com/josejuarez96/tradepartner/issues/629)).
### Changed
- `ingest._prefetch` takes `dry_run` as a required keyword; run-row and `failed_filings.json` messages share `config.clean_message` (#629).
### Fixed
- EDGAR: a NaN or infinite fact value compared against FSN no longer raises `decimal.InvalidOperation` and fails the source; it disagrees and withholds that key (#629).

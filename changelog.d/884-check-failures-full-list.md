- #884: check_failures writes its full unaccepted-failure list to edgar.cache_dir/validation/filing-failures-*.json on every run (dry runs had none) and names it before the bounded entries.
### Fixed
- EDGAR: when the filing-failure check fails a run, every unaccepted per-document failure is written to `edgar.cache_dir/validation/filing-failures-<stamp>.json`, named in the message before the first `edgar.max_validation_listed`; a dry run, which writes no `failed_filings.json`, now has a full list on disk too (#884).

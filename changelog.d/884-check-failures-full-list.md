- #884: when check_failures fails a run, its full unaccepted-failure list goes to edgar.cache_dir/validation/filing-failures-*.json, dry runs included (they had none), named first in the message.
### Fixed
- EDGAR: when the filing-failure check fails a run, every unaccepted per-document failure is written to `edgar.cache_dir/validation/filing-failures-<stamp>.json`, named at the start of the message (before the bounded first `edgar.max_validation_listed`); a dry run, which writes no `failed_filings.json`, now has a full list on disk too (#884).

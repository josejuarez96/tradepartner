- #578 part 1 (option B): EDGAR fetch-pass validation collector and _prefetch gate; parse failures listed in one bounded, redacted message plus a file under edgar.cache_dir/validation/, before any store write. Parts 2-3 wire the call sites.
### Added
- EDGAR input validation (#578 part 1): `ValidationFailures` collector and an unskippable `_prefetch` gate that fails the run before any store write, naming every recorded parse failure (bounded, redacted message; full list under `edgar.cache_dir/validation/`); new `edgar.max_validation_listed`.

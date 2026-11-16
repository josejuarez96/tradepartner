- #3191: CLI usage errors that echo a raw option value (exposure --pin/--read-at, dates, --as-of, ingest --source/--since, --cik, ...) now go through the cli_record secret scrub.
### Fixed
- CLI usage errors that echo a bad option value now scrub secrets from it (#3191).

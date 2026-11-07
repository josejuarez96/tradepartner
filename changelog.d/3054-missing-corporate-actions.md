- #3054: actions under a pre-rename symbol are asked and resolved (action alias from stored name changes); refetch-actions and --name-changes-only backfill for the sitting (step 7); STX 2021-05-11 settled duplicate
### Added
- tradepartner refetch-actions (dry-runnable, under a data release) and ingest --actions-backfill --name-changes-only
### Fixed
- Splits and dividends filed under a renamed security's pre-rename symbol are now asked and stored (up to 307 dividends and 61 splits recoverable per the 2026-11-03 measurement); STX 2021-05-11 duplicate dividend classified for retraction

- Daily bars no longer stop for live names after a NYSE_CHICAGO/NYSETX/NASDAQ_BX listing row; a notes Form 25 no longer ends the common listing (VZ). Backfill is an orchestrator step (PR #2940 comment). XOM chain: #2939.
### Added
- Fixed: the bar fetch (daily ingest and backfill windows) keeps a live name whose newest listing row is a secondary venue, as universe rule 2 does; a debt-titled Form 25 never ends a common listing (#2937).

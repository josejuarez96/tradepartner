- #796: ingest.max_dark_share (dark + snapshot-only share of listed names, off at 1.0 until measured after T45b) makes a chunk stale; _may_count reads bars ingested by t; an OTC up-listing counts as a first listing.
### Added
- `ingest.max_dark_share`: a daily or backfill chunk is stale when its dark and snapshot-only names exceed that share of the listed names (#796; default 1.0, off until measured).

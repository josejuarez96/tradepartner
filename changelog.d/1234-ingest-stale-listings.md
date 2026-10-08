- Daily ingest leaves stale listings (gap.stale_listing_sessions, ADR 0003 #1199 rule) out of max_missing_share and max_dark_share, reported in the run message; backfill unchanged (#1234).
### Fixed
- The daily price ingest no longer refuses every session over long-dead listings: a listed name dark more than gap.stale_listing_sessions sessions is reported and left out of the missing and dark shares (#1234).

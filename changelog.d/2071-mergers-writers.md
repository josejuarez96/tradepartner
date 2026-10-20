- T248d done: the daily symbol-less mergers() write and --actions-backfill's three merger types store mergers at their stamp; repair-resolution lists them unjudged
### Added
- ingest: mergers are written by the daily run and the actions backfill at announcement_date_known_at(process_date), revisions at ingested_at, counted not-yet-known, refused, unresolved, merger_self and merger_conflict; repair-resolution's dry run lists mergers rows as unjudged

- #831: `ingest --backfill --since D --source alpaca --fill-holes [--dry-run]` lists, then refetches, (security, month) holes in committed backfill months; runs are mode holes, status filled (never ok).
### Fixed
- `tradepartner ingest --backfill --since DATE --source alpaca --fill-holes` refetches bars and actions for every (security, month) in the backfill's committed months that the code now fetches but the store has no bar for (stale listing ends left KKR 2018-08..2019-07 empty); `--dry-run` lists them without fetching (#831).

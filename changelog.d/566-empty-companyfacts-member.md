- #566 (kite): an empty `{}` or cik-less member in companyfacts.zip or submissions.zip is treated as absent and asked of the per-CIK API, counted on facts_bulk_empty / submissions_bulk_empty; unblocks the backfill rerun.
### Fixed
- EDGAR: an empty `{}` member in SEC's companyfacts.zip or submissions.zip no longer fails the whole source with KeyError 'cik'; it falls back to the per-CIK API and is counted in the run message (#566).

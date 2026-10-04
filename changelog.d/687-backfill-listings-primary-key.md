- #687: cover-page listings emit one row per class, ticker and exchange per page; the read-only sweep of the real cache finds 0 store-constraint violations (was 2)
### Fixed
- EDGAR backfill no longer aborts on a duplicate listings key when one cover page lists a class's ticker twice under two titles (Honda notes, Moatable ADS) (#687)

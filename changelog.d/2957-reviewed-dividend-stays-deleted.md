- Fix #2957: the Alpaca actions source refuses a dividend on the reviewed-duplicate list, so a backfill never re-inserts a row repair deleted.
### Fixed
- A duplicate dividend retracted by repair is no longer re-inserted by a later actions backfill; the source refuses it and counts it in the run message (#2957).

- #1983 (b)+(c): a non-positive or non-finite held close halts the mark step (StaleDataError) instead of being persisted; (a) blocked on a spec amendment
### Fixed
- A held name's close of 0, negative or NaN is never written to positions_daily; the run halts with a stale_data alert naming the security and session (#1983)

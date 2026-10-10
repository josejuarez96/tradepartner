- T164d (#1419): `filing_events_as_of(t, forms, items)` reads 8-K events as of T through the master, with look-ahead cases including the filed-date trap a filed-keyed read fails.
### Added
- `store.asof.filing_events_as_of`: 8-K filing events known by T, one row per listed class, filtered by exact form and whole item code (#1419, #1358).

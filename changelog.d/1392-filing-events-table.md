- T164b (#1392, PR #1395): schema version 21 adds the empty `filing_events` table (8-K items, known_at = accepted_at), the FilingEvent record and three fixture rows; nothing writes or reads it yet (T164c-e; #1382 gates trials).
### Added
- `filing_events` table (schema version 21), `FilingEvent` record and `FilingSource.filing_events(cik)`, with three 8-K fixture rows (#1358, T164b).

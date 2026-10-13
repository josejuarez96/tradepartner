- T186 (#1628): the reviewed duplicate-dividend list (169 duplicate, 11 distinct, 3 unresolved), its loader, the pure equal_amount_groups and `repair-dividends`; dry run on ops-1549: 161 rows through 2026-01-30, 8 deferred, stale 0.
### Added
- `tradepartner repair-dividends` deletes the second record of one cash dividend stored twice, from a committed reviewed list (`store/dividend_reviews.csv`), inside a named data release (#1613).

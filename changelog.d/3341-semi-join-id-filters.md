- Id filters use a semi-join (`x IN (SELECT unnest(?))`) instead of `list_contains(?, x)` across 18 modules, ~40x faster on 10M rows (#3341).
### Changed
- Store, ingest, repair, backfill, execution and adapters SQL filters every id list with `IN (SELECT unnest(?))` instead of `list_contains(?, column)`, cutting a 500-id filter on 10M rows from 807 ms to 20 ms (#3341).

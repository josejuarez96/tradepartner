- #875: the backfill price fetch set drops non-equity rows (listing_kind) and listing rows superseded before the month, the 70% of #832 holes no bar could land on; staleness denominator unchanged (#898).
### Fixed
- Backfill months no longer fetch ids the price resolver can never assign: non-equity rows typed spac/depositary (SPAC warrants and units, bank preferred depositaries) and an older listing row superseded before the month by a later row of its security (#875).

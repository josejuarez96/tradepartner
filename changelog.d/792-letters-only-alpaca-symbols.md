- #792 part 1: only letters-only tickers (`BRK.B` form) are sent to Alpaca. The 510 digit tickers (notes such as C27C) that stopped every backfill chunk from 2019-08 are left out and named. Part 2 (equity-only fetch) waits for #788.
### Fixed
- Alpaca symbols are letters only, with at most one `.` suffix. A ticker with a digit (`C27C`, `PG25`) is a note or other non-equity line, so it is never sent and is named in the run message. Alpaca rejects such tickers, and one in a request fails the whole backfill month (#792).

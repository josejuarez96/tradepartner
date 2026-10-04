- #787 PR 1: price-quality gate in the as-of reads; zero-volume bars missing for universe and backtest, unexplained one-day jumps fail rule 6 until accepted (universe.accepted_price_jumps); health lists them. Span-start cap (AA) is PR 2.
### Added
- Price-quality gate (#787): `traded_only` price reads drop zero-volume bars for the universe and the backtest provider; `price_jumps_as_of` lists unexplained one-day jumps, which fail universe rule 6 until the owner accepts them, and `tradepartner health` prints the review list.

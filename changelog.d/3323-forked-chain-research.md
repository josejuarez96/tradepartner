- Backtest runs refuse a predecessor with two live successor chains (#3323)
### Added
- A backtest run (`StoreProvider` with `refuse_forked_chains`) now raises `ForkedChainError` when a read names a predecessor with two live successor chains at T; as-of readers and paper planning are unchanged (#3323).

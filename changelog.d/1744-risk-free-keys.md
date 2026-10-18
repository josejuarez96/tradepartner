- T170b: metrics.risk_free_source/series/basis frozen keys (defaults constant/DTB3/discount, table entries at schema 28, no fingerprint move) and DataProvider.rates(t, series) over rates_as_of; T170c wires the engine.
### Added
- Backtest: three frozen metrics.risk_free_* keys and DataProvider.rates(t, series), the store's as-of rates read (#1498, T170b).

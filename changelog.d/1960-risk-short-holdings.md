- Fix #1960: risk.check_phase reads held shorts from Ledger.short_holdings (and a negative position) as one signed book
### Fixed
- risk.check_phase reads a held short from Ledger.short_holdings, so covers, cash, one-side, short weight and gross exposure see the replayed short book (#1960)

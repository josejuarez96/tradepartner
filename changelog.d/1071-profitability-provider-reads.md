- T85c (#1071, PR #1115): provider statement_facts/sics reads (store + fake) and the fixture's profitability baseline and point-in-time cases (late 10-K, restated FY, late total_assets, five acceptance stamps).
### Added
- Backtest provider reads `statement_facts(t, ids)` and `sics(t, ids)` for the profitability family, and fixture statement facts with a scored baseline and the amendment #720 point-in-time cases (T85c, #1071).

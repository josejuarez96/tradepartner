- FakeBroker gets a public apply_split(symbol, ratio) scripting hook (#632); the run-level split tests in test_run_marks.py and test_run_trade.py use it instead of writing _net_quantity directly.
### Added
- FakeBroker: public apply_split(symbol, ratio) hook books a broker-side split without touching cash or logging a Broker call (#632).

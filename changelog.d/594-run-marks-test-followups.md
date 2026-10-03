- Tightened test_run_marks.py's seams per #594: Env.close now cuts at close(session), a rebalance-run drawdown crossing is covered, and split tests assert equity directly.
### Changed
- test(execution): tighten test_run_marks.py's close() cut, add a rebalance-run drawdown case, and assert split-test equity directly instead of via an empty kill_switch table (#594)

- #1785 T215: one session-scoped backtest-invariance fixture store, copied per case (PR #1795)
### Changed
- Tests: tests/lookahead/test_backtest_invariance.py builds its fixture store once from a session-scoped DuckDB template (tests/lookahead/harness.py) and copies it per case, each case's revisions applied as a delta (T215, #1785).

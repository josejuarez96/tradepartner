- #588 `paper check`'s four exit-criteria queries (`execution/check.py`, T66, PR #TBD)
### Added
- Paper trading: `execution.check.check(conn, settings)` runs the req 15 exit-criteria queries (rebalance count, the req 10 tracking check, incomplete chains once due, override reasons) against the latest paper window (#588)

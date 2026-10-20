- T253 done (PR #2058): a held name whose only missing bar is the read session's is kept as a skip_zero at its current weight, not sold; the alert lists kept names
### Changed
- A held name with no read-session bar is kept at its quantity for the rebalance instead of sold left_universe; the other names share the remaining budget (#2023, T253)

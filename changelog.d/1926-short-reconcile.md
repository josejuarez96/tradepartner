- #1926 T233 reconciliation of shorts: negative quantity compared, short_external_change, short dividend band [due, 0] (PR #1929)
### Added
- Execution: reconcile.compare reads a ledger short as a negative quantity, halts an unexplained change as short_external_change and passes a debited or undebited short dividend (T233, #1926)

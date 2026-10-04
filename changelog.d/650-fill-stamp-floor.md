- #650: `journal.append` refuses a fill stamped at or before the latest reconciliation, so a tied clock reading can no longer drop a fill from the ledger's cash (fails closed instead of a false cash mismatch).
### Fixed
- `store.journal.append` refuses a `fills` row whose `known_at` is not strictly after every reconciliation's, so `ledger.from_journal` never drops a fill that ties its base reconciliation (#650).

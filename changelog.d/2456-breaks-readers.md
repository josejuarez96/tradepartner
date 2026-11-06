task: T282c (PR #2457)
- T282c: run.py readers and paper reconcile accept a breaks cash base; halt stays
### Added
- Remaining breaks cash-base readers (run.py exit book and run ledger, CLI reconcile guard) accept a breaks row with broker_cash; the T281 halt still submits no order (#2456, T282c).

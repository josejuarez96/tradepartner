- #488 fixed (PR #502): reconciliation's journal reads are cut at an explicit as_of; explanations_as_of and reconcile_now take it, paper reconcile and resume pass their clock; T63 picks step 4's value (see #488).
### Fixed
- Reconciliation's ledger and explanations read only journal rows known at an explicit `as_of`, so `explanations_as_of` no longer depends on when it runs (#488).

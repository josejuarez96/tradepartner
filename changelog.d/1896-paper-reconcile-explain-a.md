- Paper reconcile: a spin-off child the broker books inside an open window now journals a `spinoff_receipt` from the stored spin-offs known at close(S-1) (#1896)
### Fixed
- `reconcile_run` passed `spinoffs={}` to `reconcile.compare`, so a broker-booked spin-off child inside an open window failed closed; it now offers the stored `spinoff` actions whose ex-date is in `(base session, S]` and whose parent the ledger held on the session before the ex-date, while a row known only after the cutoff, a wrong ratio, or a child for a parent the ledger never held still halts (#1896).

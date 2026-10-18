- #1761 A held name's ticker change known at close(S-1) reconciles in `paper reconcile`
### Fixed
- Execution: a held name whose stored `symbol_changes` ticker change is known at close(S-1) reconciles -- the broker position under the new symbol matches the ledger's under the old one, quantity equal -- and the `reconciliations` row records the mapping; a change not known at close(S-1), a quantity difference under the new symbol, or two candidate symbols still halts (#1761).

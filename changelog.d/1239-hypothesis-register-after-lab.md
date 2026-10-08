- T104c: `hypothesis register` returns an unchanged file's record by canonical set and refuses prose-only edits in every state; on a lab store it takes only promoted files (else: write a one-value sweep); pre-lab stores keep Phase 3.
### Added
- `hypothesis register` after the strategy lab: only a promoted file (`promotion_of`, its variant's fingerprint, the family rules, a feasible anchor) registers on a lab store; a store without the lab keeps the Phase 3 rules (T104c, #1239).
### Changed
- `hypothesis register` refuses a prose-only edit (same fingerprint, new doc hash) and returns an unchanged file's record by its canonical frozen set, in every state (T104c, #1239).

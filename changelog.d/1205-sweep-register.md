- T104 sweep register in an existing family: sweep.register applies the store-level refusals (oracle, family rules, fingerprint, readiness, anchors, family lattice) before any row, then writes the sweep and its variants (#1205).
### Added
- `sweep.register`: registers a sweep file in an existing family with every store-level refusal of strategy-lab req 1 before any row is written; idempotent for an unchanged file (T104, #1205).

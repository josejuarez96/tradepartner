- T104b: `sweep register` fixes a new family's `family_rules` at its first sweep (FAMILY_PARENTS parent, live caps); refuses holdout overlap and a child before its parent's holdout end or spend; child mark seeded from the parent's.
### Added
- `sweep register` registers a new family's first sweep and writes its family rules once: parent from `FAMILY_PARENTS`, no holdout overlap with another real family, a child only after its parent's holdout is spent or capped, with the parent's SR* mark as its seed (T104b, #1225).

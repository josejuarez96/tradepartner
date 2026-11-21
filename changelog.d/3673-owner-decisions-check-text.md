- #3673: one owner_decisions.kind CHECK text on a fresh, a migrated and a repaired store; the schema-equivalence baselines are empty.
### Fixed
- A store that got the strategy-lab schema after the later versions' decision kinds listed `owner_decisions.kind`'s values in another order than a store that migrated through version 16. `apply_lab_schema` now writes them in one order (`lab_schema.OWNER_DECISION_KINDS`) whenever it rebuilds the table; a store that already allows every kind is not rewritten. (#3673)

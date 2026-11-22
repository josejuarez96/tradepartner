### Added
- tests/store/test_schema_equivalence.py: a fresh store is compared with one migrated from versions 2, 3 and 4, column by column, constraint by constraint and index by index; today's differences (no lab tables on a fresh store, the owner_decisions.kind CHECK order) are a pinned baseline (#3624).

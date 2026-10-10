- T160b (#1442): `hypothesis register --operations-book-of <variant> --reason` registers an operations file (a paper book's machine test, never a promotion) with an `operations_book` decision; schema 22.
### Added
- `hypothesis register --operations-book-of <variant-slug> --reason`: an operations file of a sweep variant (strategy-lab spec req 1, amendment 2026-10-10; ADR 0017 part A), the `operations_book` decision kind (schema version 22) and its `lab status` line; never a promotion, never a holdout spend.

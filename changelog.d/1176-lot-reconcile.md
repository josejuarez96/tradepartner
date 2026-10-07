- T90 (#1176): `paper lots-reconcile` compares a broker 1099-B export with the lot ledger, read-only; the export parser is a stub until the owner's first real export.
### Added
- `tradepartner paper lots-reconcile --export --tax-year`: compares the broker's 1099-B export with the lot ledger on proceeds, cost basis and box 1g, reports every difference with both figures and writes nothing (T90, #1176).

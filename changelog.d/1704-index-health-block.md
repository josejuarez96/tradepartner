- T189b (#1704): health's coverage block checks the S&P 500 answer key per sample session (hits by security, misses by rule:reason or unresolved, key SHA); index_members_missing gates only once coverage.index_max_missing is set.
### Added
- The index answer key in `health` (#1682, T189b): `coverage_reference.index_members`, the `coverage.index_members` / `coverage.index_max_missing` keys, two `health --coverage` lines, the page card's answer-key table, and the check-only source scan and byte-identity tests.

### Changed
- Spec: `snapshot_static` attributes apply before fetch time only to columns of a row already visible at T; as-of reads and the truncation harness treat the row itself strictly by `known_at` (#35).

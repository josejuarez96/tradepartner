- T280a: schema version 37 adds the append-only reconcile_breaks journal and widens four CHECKs (breaks, auto/owner true-up, skip_restricted); no order-path behavior yet (#2277).
### Added
- Schema version 37: the `reconcile_breaks` journal table, with one open break per window and name held by `journal.append`, and the `breaks`, `auto_true_up`, `owner_true_up` and `skip_restricted` values (ADR 0025, T280a).

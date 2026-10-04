- #784: backfill and daily staleness count only benchmarks and common names on universe.exchanges (OTC out); a snapshot_static-only name with no bars is reported, not counted. 2016-01 goes from 570/2,335 missing to 0/1,756 (179 reported).
### Fixed
- Staleness (backfill month and daily check) no longer counts OTC names, and reports instead of counting a snapshot_static-only name with no bars in the window (#784).

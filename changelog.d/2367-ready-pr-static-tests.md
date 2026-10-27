- ready_pr runs the whole-tree static invariant tests locally (-n 2, ~25 s) before every push
### Changed
- ready_pr now always runs a STATIC_INVARIANT_TESTS set (boundary, literal, import, flag-by-name, no-write, dashboard store, reason/alert-kind tests) before the push; a failure stops with NOT READY (#2367)

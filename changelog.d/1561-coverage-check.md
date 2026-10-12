- T180 (#1561): health's coverage check against a named reference list (coverage.* config, health --coverage, on-demand page card); report-only until T181 flips coverage.check_enabled.
### Added
- `health --coverage` and the data-health page's coverage card: the coverage check against a named reference list (`coverage.reference_names`, CIKs pinned), with late-known listings and a snapshot cross-check; `coverage_reference_names` fails `--check` only while `coverage.check_enabled` is on (#1541, T180).

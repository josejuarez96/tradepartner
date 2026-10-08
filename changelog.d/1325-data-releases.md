- T140 (#1325): named data releases runbook (docs/runbooks/data-releases.md): the rule, the data/releases.toml record, the steps, and the trial 4 back-fill (pre-sweep-20261008 backup, vintage equal).
### Added
- Runbook for named data releases: every row-changing store repair is a named release with a backup and before/after records in data/releases.toml, plus the back-fill naming the backup H1's holdout trial read (T140).

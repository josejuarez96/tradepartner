task: T393 (PR #3418)
- T393: decision data-release close refuses a bar, action or master-row release without a passing, current coverage record; the check reads retracted_rows (reason, rule, unreceived spans exit 1) and fails ownerless bars and dividends.
### Added
- Release coverage is mandatory at close (ADR 0029): decision data-release coverage --record writes the verdict, close requires it.

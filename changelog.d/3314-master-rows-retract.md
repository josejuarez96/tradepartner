task: T392 (PR #3317)
- T392: delete-master-rows retracts into retracted_rows (ADR 0029); rule is the manifest lines
### Added
- delete-master-rows moves the reviewed never-true master rows into retracted_rows instead of deleting them (restorable); refusals and master-retract unchanged

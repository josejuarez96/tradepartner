task: T398 (PR #3441)
- T398: trial-reproduce reruns a registered trial on a copy rebuilt as it started (retracted rows back, later backfills out), writes nothing
### Added
- `trial-reproduce <trial_id>` reproduces a registered trial after a repair on a temporary copy of the store (ADR 0029, T398); holdout trials need `--holdout-repeat`; a changed data vintage or code tree is reported as not reproducible.

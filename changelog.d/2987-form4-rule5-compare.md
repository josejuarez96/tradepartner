task: T351 (PR #2993)
- T351: Form 4 rule 5 comparison in the ingest driver (#2714): threshold-gated markers in the manifest, run fails at/above it, health counts; key still unset.
### Added
- Form 4 rule 5 comparison in the ingest driver: with events.form4_compare_threshold set, a below-threshold mismatch stores the trusted row and an unresolved marker in the writer manifest, markers carry forward and recheck on a changed zip, and a share at or above the threshold fails the run; unset keeps the order-sample refusal. Health reports the counts.

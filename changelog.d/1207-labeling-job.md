- T123 (#1207): research.labeling.job: run_batch opens the run first, spend_check over every records file, C5 drift run before a frame batch, paced calls, C8 records redacted at full length, shortlist; scripted double only
### Added
- Research labeling job (T123): `run_batch`, `spend_check`, the drift probe, the per-call records and the batch shortlist, with `dry_run`; no real model call in tests.

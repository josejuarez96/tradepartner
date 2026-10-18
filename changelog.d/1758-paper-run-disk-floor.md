- Paper run refuses to start below paper.min_free_disk_gb free on the store's disk: run_failed alert, no run row, no kill switch (#1758, PR pending).
### Added
- paper.min_free_disk_gb (default 5, 0 = off): a paper run below it refuses before any broker call with a run_failed alert and no kill switch (#1758).

- #396 collect: a write-off back-fill failure after a rejection verdict raises RejectionCapError chained from it, so the run's verdict is never lost to the committed cursor
### Fixed
- Execution: a failed write-off back-fill no longer loses the collection's rejection-cap verdict (#396)

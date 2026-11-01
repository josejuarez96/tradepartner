- T319a: SEC daily listing and cached files, conditional quarterly index, pure file cursor (#2741)
### Changed
- Incremental EDGAR raw requests use the shared limiter, cache daily files locally, retain changed quarterly snapshots, and select only new or re-posted listed files.

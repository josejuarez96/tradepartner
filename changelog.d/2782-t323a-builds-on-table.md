task: T323a (PR #2822)
- T323a: EDGAR builds and readers use the stored filing index with first-known filed_on and SEC listing order (PR #2822).
### Changed
- Master, classification, delisting, 13D and stamp readers read the stored filing index when bound; a 13D correction crossing the backfill cutoff is counted `sec_forms_cutoff_corrected` and changes no derived row (#2782).

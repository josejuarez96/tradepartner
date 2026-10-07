- T113 (#1195): schema version 16 "L", the lab migration: a migrating store gets the lab tables and marks every existing hypothesis pre-lab, with fingerprints and family rules; the owner's store takes it at its next writing job.
### Added
- Schema version 16 (strategy-lab T113): the lab migration creates the lab tables, marks every existing hypothesis pre-lab, writes one fingerprint per hypothesis and one family-rules row per family; fresh stores stay without the lab tables.

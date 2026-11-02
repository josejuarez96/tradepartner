- T323 --rebuild-index checks the disk floor before every quarter chunk (#2841)
### Fixed
- --rebuild-index now stops at a chunk boundary, resumable, when free disk drops below ingest.min_free_disk_gb mid-seed (#2841)

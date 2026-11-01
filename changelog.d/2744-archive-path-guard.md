task: T345 (PR #2746)
- T345 (#2744): local raw archive path setting (TRADEPARTNER_RAW_ARCHIVE, .env only, unset = off) and the post-write disk-floor guard (raw_archive.min_free_disk_gb, default 10 GB, fail closed) for T346
### Changed
- Config: `TRADEPARTNER_RAW_ARCHIVE` names the local raw archive (ADR 0028), held in `.env` only and redacted like a secret; `raw_archive.min_free_disk_gb` (default 10) refuses an archive write that would leave less free space, counting the file (#2744).

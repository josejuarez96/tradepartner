task: T346 (PR #2753)
- T346 (#2751): store/raw_archive.py, content-addressed raw archive (atomic SHA-256 writes, dedup by name, evict only after confirmed upload, download-and-verify before re-derive) and a source scan keeping archive openers out of readers
### Added
- Raw archive helper (`store/raw_archive.py`, T346): content-addressed raw bodies and manifests, bounded iCloud upload/download waits (`raw_archive.*` wait keys), and a reader-boundary source scan (#2751)

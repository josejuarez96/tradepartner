task: T361 (PR #2767)
- T361a: cache migration runner moves dead cache versions and the corpus raw into the archive (journal, resume, tombstone index); backup and restore follow
### Added
- Existing-cache migration runner for the move class (journal, resume, duplicate rule, eviction, manifest, tombstone index); backup copies and restore are later slices.

task: T361b (PR #2776)
- T361b: the migration runner copies backup-class files (stamps) into the raw archive by hash, floor-checked, confirmed and evicted; manifest holds the full snapshot.
### Added
- Existing-cache migration runner, backup class: stamps are copied, never moved, into the raw archive (T361b).

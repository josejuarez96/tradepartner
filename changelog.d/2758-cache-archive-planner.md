task: T360 (PR #2760)
- T360 (#2758): the existing-cache migration planner classes each EDGAR cache file as move, back-up or keep per the #2743 table, and projects bytes to free and free space per phase; `raw_archive.migrate_extra_paths` (empty) added.
### Changed
- Existing-cache migration planner (`store/raw_archive_plan.py`, pure) and the `raw_archive.migrate_extra_paths` key (empty by default, validated; refuses `bulk/submissions.zip` while `edgar.bulk_stamps_enabled` is on) (#2758).

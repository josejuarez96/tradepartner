- Release runbook: how to restore a manifest-less legacy copy; example backup_path uses the printed .bak-<stamp> form (#2144)
### Fixed
- docs(runbooks): data-releases.md says how to restore a legacy release copy with no manifest (skip the wal_bytes check; confirm no <copy>.wal, open it read-only once, restore without a WAL), and the example entry's backup_path now uses the printed .bak-<stamp> form (#2144)

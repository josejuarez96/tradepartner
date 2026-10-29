- T266a2 PR 2: CLI schema migration gates and recovery runbook (stacked on #2568)
### Added
- CLI ingest and paper writes now take verified pre-migration copies before upgrading an older store; dry runs and read-only commands refuse older schemas (#2565 #2160).

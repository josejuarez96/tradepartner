task: T266a2 (PR #2569)
- T266a2: store migration takes a verified pre-migration copy; only ingest, backfill and paper writes migrate; other writers and dry runs refuse an older store; kill never migrates
### Added
- Store migration through ingest, backfill and paper write commands takes a verified pre-migration copy first; other writers and dry runs refuse an older store (#2565, #2160).

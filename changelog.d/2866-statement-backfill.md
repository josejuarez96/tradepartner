task: T365 (PR #2871)
- T365: `ingest --statement-backfill --bulk-from-cache` fills statement_facts for added names only, chunked and resumable; the owner runs it in T368 (PR #2871)
### Added
- `tradepartner ingest --source edgar --statement-backfill --bulk-from-cache`: an additive, resumable statement_facts backfill for added statement names, with `edgar.statement_backfill_chunk_ciks` (#2826, T365)

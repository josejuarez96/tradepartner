- T77b (#1070, PR #1114): statement facts ingest writes first vintages per CIK behind edgar.statement_facts_enabled; ingest --bulk-from-cache and --rebuild-statement-facts. T78 (owner re-ingest) is next.
### Added
- Statement facts ingest (#660, T77b): first vintages, hold/late/restated counts, derived gross profit, one bulk insert per CIK; `ingest --bulk-from-cache` and `ingest --rebuild-statement-facts`.

- T77a: EDGAR statement_facts adapter (switch-gated, per-CIK cache stamped at read time, conflict keys and fetch-pass counts), reuse_cached bulk path, recorder trimmer keeps statement tags (PR pending).
### Added
- EDGAR `statement_facts(cik)` adapter behind `edgar.statement_facts_enabled`: one companyfacts read per CIK fills the shares and statement caches, entries stamped at read time, conflicts on `statement_conflict_keys`; `bulk_company_facts(reuse_cached=True)` reuses the cached zip with no request (#660, T77a).

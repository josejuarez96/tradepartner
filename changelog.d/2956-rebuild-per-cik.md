- #2956 (PR #2959): statement_facts / filing_events rebuilds replace per CIK; a CIK whose read is empty (missing cache) keeps its rows, counted rebuild_kept_empty_read.
### Fixed
- A statement-facts or filing-events rebuild no longer deletes a company's history when its read comes back empty (missing statement cache); it replaces per CIK and counts the CIKs it kept (#2956).

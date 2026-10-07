- T78 (#1127): statement facts on by default after the real-store re-ingest (2.13M rows added; health --check fails only on two owner-accepted same-day pairs); EDGAR company-facts fixtures re-recorded with the statement tags.
### Added
- Statement facts are on by default (`edgar.statement_facts_enabled = true`, T78, #1127) after the real-store re-ingest passed; the EDGAR company-facts fixtures now carry the statement tags, cut at their recorded filing date.

### Added
- Data: EDGAR per-filing failure policy: `failed_filings.json`, quarantine, `check_failures()` and `record_failures()` as the ingest `after_commit` hook; config `edgar.max_filing_failures`, `edgar.min_failed_filings`, `edgar.max_failed_filing_share`; the EDGAR run message reports failed, quarantined and facts-missing counts (#263).

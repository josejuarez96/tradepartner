### Changed
- Plan: T11c rewritten around the FSN data sets (`edgar_raw.fsn_zip`, `parse_fsn`, `edgar.fsn_first_year`, `edgar.header_forms`, `edgar.header_first_year`; classification call site on `edgar.header_forms`); new T11d (delistings, filing failure policy with `edgar.max_filing_failures` and `edgar.max_failed_filing_share`, backfill measurement); T19 depends on T11d (#216).

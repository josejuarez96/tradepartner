- EDGAR backfill handles a company-facts payload with facts but no cik: identified by the requested CIK, counted, no longer a KeyError
### Fixed
- edgar: a company-facts payload with facts but no cik no longer crashes the backfill (KeyError 'cik'); it is identified by the CIK it was requested under (zip member or API URL), counted on facts_bulk_keyless/facts_api_keyless in the run message, and its facts carry that CIK (#599)

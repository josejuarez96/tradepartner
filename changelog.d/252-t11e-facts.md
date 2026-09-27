### Added
- Data: `EdgarFilingSource.facts` (T11e): company facts plus per-class cover shares from FSN and lag-window documents, stamped at read time, dated per the owner's 2026-09-26 rule and de-duplicated across sources; `facts_as_of` serves the latest-ingested row per (security, fact name, class, filing accession) (#252)

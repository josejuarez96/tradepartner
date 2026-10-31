- Plan and spec: filing_index keeps a later SEC filed_on as a dated revision (key cik, accession, known_at); rebuild source is the stored revisions; backfill cutoff reads the first-known filed_on; T318b split out (#2703).
### Changed
- Data-foundation plan and spec: filing_index stores filed_on corrections as revision rows, T318 split into T318 and T318b (#2703).

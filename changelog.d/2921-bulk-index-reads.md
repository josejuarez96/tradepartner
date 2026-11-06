- EDGAR incremental index: candidate rows, touch-state CIKs and shared-accession listing orders are read in one filing_index scan each, not one per chunk, CIK or accession (#2921, PR #3035); cache-key parsing split to #3032.
### Fixed
- The incremental EDGAR index read scans filing_index once per read, not once per 500-accession chunk, new-issuer CIK, relevant accession or shared accession (#2921).

- T187 follow-ups: write_master's chain path now gates held/unjudged and moves like ingest; chains parse the CIK; spec and T187d runbook corrected (docs)
### Changed
- Chains code parses each security id's CIK at its ':' or '@' separator instead of assuming the first ten characters; the data-foundation spec and T187d runbook say so (docs).
### Fixed
- write_master's chain path now applies the held and unjudged gates and retracts-then-rewrites a moved chain, sharing ingest's writer (#1838).

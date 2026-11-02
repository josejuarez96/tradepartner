- #2616: the EDGAR filing index reads one CIK's stamps at a time and shares row strings; full-history peak about 16 GB to about 4 GB on the real cache (still grows with history; plan in the PR)
### Fixed
- EDGAR filing index no longer holds every issuer's full stamps beside every index row; its full-history peak falls from about 16 GB to about 4 GB (#2616).

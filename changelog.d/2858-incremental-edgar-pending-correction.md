- Incremental EDGAR keeps a filed_on correction seen while pending (#2858)
### Fixed
- Incremental EDGAR no longer drops a filed_on correction seen while the accession is pending: the stamp night re-reads the later daily listings and writes the same revision as a rebuild (#2858).

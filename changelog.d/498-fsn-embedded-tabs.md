- #498 FSN members with unquoted tabs in free text (txt.tsv value, dim.tsv segments) are folded into that column with U+FFFD instead of failing the whole EDGAR backfill; a sweep of all 56 periods 2015–2026_08 reads and parses
### Fixed
- Ingest: unquoted tabs inside an SEC FSN free-text column (txt.tsv value, dim.tsv segments) no longer fail the EDGAR source; a kept listing value or class member that had one fails only its accession (#498)

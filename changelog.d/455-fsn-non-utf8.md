- #455 FSN members with a non-UTF-8 byte (a 2015 SandRidge txt.tsv note) are read with the byte replaced by U+FFFD instead of failing the whole EDGAR backfill
### Fixed
- Ingest: one non-UTF-8 byte in an SEC FSN member no longer fails the EDGAR source; invalid bytes are replaced, valid UTF-8 kept (#455)

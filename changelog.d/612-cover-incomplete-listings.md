- EDGAR per-document cover parses now cache and count their skipped incomplete listings (`cover incomplete listings` in the run message), as FSN's are; no cache version bump ([#612](https://github.com/josejuarez96/tradepartner/issues/612)).
### Added
- EDGAR: `.cover_incomplete_listings` counts listings a per-document cover parse skipped (no title or exchange), shown in the edgar run message; the cover cache entry records them too, with no `COVER_VERSION` bump (#612).

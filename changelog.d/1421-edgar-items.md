- T164c (#1421): the EDGAR adapter keeps 8-K `items` (stamps layout 2, `STAMPS_LAYOUT`), refreshes old-layout CIKs once from `filing_events(cik)` and answers it from the stamps; old stamps byte-identical.
### Added
- EDGAR adapter keeps the submissions `items` field per filing and answers `filing_events(cik)` from the stamps cache (T164c, #1358).

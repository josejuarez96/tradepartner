### Added
- Phase 2 T6: as-of read primitives (`prices_as_of`, `adjusted_prices_as_of`, `facts_as_of`, `listings_as_of`) returning the latest revision as of a tz-aware T, with a bare date or naive datetime rejected; the truncation-invariance harness (`tests/lookahead/harness.py`) and its invariance suite over every distinct `known_at` in the fixture (#39).

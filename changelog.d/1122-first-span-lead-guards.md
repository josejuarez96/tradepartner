- #1122 Backfill's first-span lead reads the resolver's own span, not a recomputed listing row; pinned the clock `_price_chunk` builds its resolver at; added a truncation-invariance case for lead bars (PR #TBD)
### Added
- Tests: a first-span-lead case in the truncation-invariance harness (tests/lookahead/test_universe_invariance.py), and a test pinning the clock `_price_chunk` builds its store resolver at (#1122, #980, #990)
### Fixed
- Backfill: `_led` now reads the resolver's `first_span_lead` directly instead of recomputing the first ticker-bearing listing row, so an unreadable ticker or a dropped same-day typo can no longer disagree with which span the resolver treats as first (#1122, #990)

task: T383 (PR #3160)
- T383: `paper cost` measures a book's per-side cost against the open from its own orders; daily window 5 reads +61.4 bp pooled, 60 to freeze (#3160)
### Added
- `paper cost --book <b> [--window <id>] [--through S]`: the measured per-side cost against the session's bar open, per order, weighted by notional, stops left out, rounded to 5 bp (#3118 item 3).

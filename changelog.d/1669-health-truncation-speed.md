- CI shard 4 back to ~10-20 min: the health truncation test computes only its two rules and _rate_rules memoises rate_known_at per vintage (#1669).
### Fixed
- The health truncation test no longer recomputes the FRED rate rules at every probe, and the rate-stamp check computes each vintage's known_at once per call (#1669).

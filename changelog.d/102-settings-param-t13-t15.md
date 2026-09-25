### Changed
- Plan: T8b, T9, T13 and T15 lines specify an explicit `settings` parameter on the read-time functions (`listings_as_of`, classification, `universe_as_of`, `survivorship_gap`), passed down each call chain, with override tests, so a frozen per-trial `Settings` governs every as-of read (#102).

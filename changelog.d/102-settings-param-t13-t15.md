### Changed
- Plan: T13 and T15 lines specify an explicit `settings` parameter on `universe_as_of` and `survivorship_gap` (passed through from the gap to the universe) and an override test each, so a frozen per-trial `Settings` governs every as-of read (#102).

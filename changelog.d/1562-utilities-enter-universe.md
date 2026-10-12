- T176 merged: universe.exclude_sic_ranges defaults to none, value guard replaced by a shape check, health guarded_sic_default rule removed; stored registrations keep [[4900, 4999]].
### Changed
- Utilities enter the universe: `universe.exclude_sic_ranges` defaults to `()`, only its shape is validated, and `health --check` has no `guarded_sic_default` rule (ADR 0006 amendment 2026-10-11, #1540).

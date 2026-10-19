- T211c: the opt-in frozen key `adjust.apply_spin_offs` (default false), refused by `load_frozen` until T211e applies it (spec amendment #1894, item 3) (PR #1918)
### Added
- Config: `adjust.apply_spin_offs` (default false) as a new frozen key: not required of a file, frozen at its default when absent, outside the promotion identity, and a `NOT_YET_APPLIED_KEYS` member so a `true` registration is refused until T211e.

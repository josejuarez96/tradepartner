- #1984: a run verifies frozen_json against frozen_sha256 and halts (LimitBreachError) on a mismatch
### Fixed
- A paper run halts before any broker read when the window's frozen_json no longer hashes to its frozen_sha256 (limits edited in place); the halt re-fires every run until the row is restored (#1984)

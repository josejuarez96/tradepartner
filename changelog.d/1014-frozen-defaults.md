- T96 (#1014): backtest/frozen.py (FROZEN_KEY_DEFAULTS with schedule.* = month_end, frozen_values, canonical_frozen_set, fingerprint); schedule is a frozen section and load_frozen reads pre-lab registrations at month_end.
### Added
- Frozen-key defaults: `schedule.*` is frozen per hypothesis, a registration without it reads `month_end` at its stored hash, and `frozen.fingerprint` hashes the keys that decide a run (T96, #1014).

- #1022: frozen.is_default compares a frozen value with its table default in JSON form (True, 1 and 1.0 differ); the canonical set and frozen_hash_matches use it.
### Fixed
- The canonical frozen set and frozen_hash_matches no longer treat True, 1 or 1.0 as equal to a different-typed table default (#1022).

- #1022: frozen.canonical_frozen_set leaves a key out only when it is its table default in the same JSON form (True, 1 and 1.0 differ).
### Fixed
- The canonical frozen set no longer treats a stored True, 1 or 1.0 as equal to a different-typed table default (#1022).

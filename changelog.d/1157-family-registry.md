- T128: FamilySpec drives per-family tables and strategies._SIGNALS; momentum fallbacks go (inert_sections, fingerprint, required_keys, frozen_params_of, frozen_hash_matches raise on unlisted family); H1 and TWIN pins unchanged.
### Changed
- The family registry: config.FAMILIES is the source of truth; per-family tables and backtest/strategies.py signal records derive from it; the momentum fallbacks on an unlisted family are gone (ADR 0014 point 2, T128, #1157).

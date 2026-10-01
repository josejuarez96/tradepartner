- #377 Schema version 7: decisions.reason is a nullable closed set (schema.DECISION_REASONS, used by plan); the v6 migration rebuilds decisions, refusing on a stray reason; spec list closed
### Added
- Schema version 7: CHECK on decisions.reason (left_targets, left_universe, exclude_name, keep_name, delisted, untargeted_receipt, window_stop or null) with shared constants, a migration from version 6 or 5 that rebuilds decisions, and the spec list closed to match (#377)

### Added
- Phase 2 T7: `PriceSource` interface (`adapters/prices.py`) with validated `Bar`/`CorporateAction` records and the timing rules as pure functions (`bar_known_at`, `action_first_seen_known_at`, `revision_of`: revisions stamped at ingest, never back-dated); `FixturePriceSource` replays the fixture universe by `security_id` only and enforces the timing contract at load (#75).

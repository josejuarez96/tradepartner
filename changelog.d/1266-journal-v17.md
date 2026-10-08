- #1266 T132: schema version 17, the ADR 0015 expansion seams (plan T132)
### Added
- Store: schema version 17 adds `book_id` to the eight journal tables, `position_side` to five and the `orders` shape columns, all defaulted; `require_journal` raises `SchemaVersionError` on a version-16 store and the journal readers catch it (#1266, #1261).

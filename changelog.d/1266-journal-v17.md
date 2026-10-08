- #1266 T132: schema version 17, the ADR 0015 expansion seams (plan T132)
### Added
- Store: schema version 17 adds `book_id` to the eight journal tables, `position_side` to five and the `orders` shape columns, all defaulted; a version-16 journal makes `require_journal` raise `SchemaVersionError`, and the window commands, ops and override pages and lots CLI surface its "open it for writing once" message (#1266, #1261).

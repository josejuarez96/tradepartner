- T154 (#1370): schema version 19 adds alerts.book_id (default main, every row kept); open_window, latest_window and the locked/no_window dedupe read per book; with no book every read is as before, so H1's book main is unchanged.
### Added
- Schema version 19: `alerts.book_id` (default `main`), per-book `open_window`, `latest_window` and the session-scoped alert dedupe (ADR 0017 B.2, B.6; T154).

task: T389 (PR #3285)
- T389: schema 49 retracted_rows side table with pure retract and restore (ADR 0029); no repair wired yet
### Added
- Schema 49: `retracted_rows` side table and `store.retracted_rows.retract`/`restore` (ADR 0029, T389); rows move out of bars, actions, listings and delistings whole and come back unchanged.

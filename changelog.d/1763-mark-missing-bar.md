- Paper marks: a tradable held name missing its S-1 bar is marked at its last close with a stale_mark alert for at most paper.max_stale_mark_sessions (3, frozen) sessions, then fails as before (#1763).
### Changed
- A tradable held name missing its S-1 bar no longer fails the paper run at once: marked at its last close with a stale_mark alert for up to 3 consecutive sessions (frozen key paper.max_stale_mark_sessions; windows frozen earlier keep the old failure) (#1763).

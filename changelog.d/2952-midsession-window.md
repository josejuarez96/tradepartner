task: T377 (PR #2953)
- T377 (#2953): per-book mid-session submit window: `submit_at = midsession` books submit in [open+60, open+120] min (latest batch start end − sell wait − 10 min); `open` books unchanged.
### Added
- Paper: `paper.execution.<book>.submit_at = midsession` with a mid-session submit window (default 10:30–11:30 ET) and latest batch start; validator refusals for a mid-session book (T377, #2952).

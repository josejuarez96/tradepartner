task: T391 (PR #3415)
- T391: repair-restore puts retracted rows back by release or key, in its own open release, dry run first
### Added
- tradepartner repair-restore: restore retracted rows (ADR 0029) by release or logical key; dry run, --expect-rows, refuses inside an open paper window, prints the release close with the restored sessions

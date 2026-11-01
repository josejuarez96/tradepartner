task: T362 (PR #2784)
- T362 done: archive migrate-cache (dry run by default, --apply refuses on the store lock) and archive restore commands; the departure corpus refuses a tombstoned miss before any request.
### Added
- tradepartner archive migrate-cache [--apply] and archive restore --path [--run]; corpus fetch departure-reason refuses, naming the restore command, when it needs a file the migration moved; runbook uses migrate-cache instead of rm -rf.

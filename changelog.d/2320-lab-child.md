task: T290 (PR #2327)
- T290 built: the Lab's child process (argv, spawn, log, scrubbed tail); PR #2327 in review
### Added
- The Lab's child process helper (dashboard/lab_child.py): exact argv for sweep run, backtest and sweep register, a never-waited spawn with a 0600 log, the live-store refusal and a secret-scrubbed log tail.

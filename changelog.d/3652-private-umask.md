- The tradepartner command sets umask 077 at entry: new store, backup, archive and research files are owner-only; a one-time chmod step for existing files is in the scheduling runbook (#3652).
### Changed
- `tradepartner` (`cli.main`) sets the process umask to 077 before anything else: files and directories it or its children create are owner-only (0600/0700). Existing files keep their mode; docs/runbooks/scheduling.md has the one-time chmod step for a store made earlier (#3652).

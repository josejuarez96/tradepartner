- repair-resolution's orphan guard: --allow-orphans refuses a dry run or a negative count, the orphan display name is as-of the run, and the held-ticker dict is renamed (#3270).
### Fixed
- repair-resolution refuses --allow-orphans with --dry-run and a negative --allow-orphans as usage errors, reads the orphan display name from the latest non-retracted securities row known at the run, and names _held_tickers' own-row dict accurately (#3270).

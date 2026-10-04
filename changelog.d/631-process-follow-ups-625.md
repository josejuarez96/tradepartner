- #631 process follow-ups from #625's reviews: tier table reads top-down (docs under .claude/agents/ or docs/runbooks/ run on Opus), reviewer verification passes skip main's hunks, NUL-safe path list
### Changed
- agents.md tier table reads top-down, first match wins; a docs-only task under `.claude/agents/` or `docs/runbooks/` runs on Opus (#631)
- Reviewer verification passes: NUL-separated path list, main's hunks next to a conflict resolution are told apart with `git log --first-parent --no-merges -p` and the merge's combined diff; spec-critic skips merged-in main too (#631)

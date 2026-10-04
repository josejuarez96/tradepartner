- T75: the merge train in git-workflow (rule 7, PR lifecycle, commands, enforcement table), ADR 0002 amendment, teams.md command card, development-process gate, CLAUDE.md rule 1, non-strict ruleset file (#673, #529)
### Changed
- Ways of working: PRs land through the merge train; only the owner's "merge train <batch id>" merges; `merge_train.py` commands documented; git-workflow enforcement table for a public repo; ADR 0002 dated amendment; teams.md command card rows and the record-directory exception; CLAUDE.md rule 1 clause (#673, closes #529)
- `.github/rulesets/protect-main.json`: `strict_required_status_checks_policy` false; repository admin role (`RepositoryRole` 5) as a `pull_request`-only bypass actor; activation and update commands in git-workflow.md (owner applies it in T75b)

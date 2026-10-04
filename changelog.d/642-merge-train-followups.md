- Hardened merge_train.py's merge/prune commands against ten gaps found in the #640 safety and quant reviews (branch-deletion fail-closed, merge-lock coverage, timeout handling, fetch races).
### Fixed
- merge_train.py: branch deletion, pr_data scope, gh pr merge timeout and retry classification, FETCH_HEAD race, prune worktree/lock safety, MERGED confirmation, and stop-reason accuracy (#642)

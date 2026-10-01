- execution: reattempts.py shares plan.FORCED_EXIT instead of its own literal (#497, PR #531)
### Changed
- reattempts.py imports plan.FORCED_EXIT (now public) instead of defining a duplicate _FORCED_EXIT literal; no behaviour change

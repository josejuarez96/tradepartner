- Tooling (#1130): ready_pr leaves pytest to the sharded CI by default (--tests runs the mapped tests); team.py counts a task whose code PR merged as done before its plan box is ticked.
### Changed
- ready_pr no longer runs pytest locally by default: CI runs the full suite (--tests runs the mapped tests, --full-tests all); team.py counts a plan task whose code PR is squash-merged on the plan ref as done, so a fold no longer gates its dependants (#1130)

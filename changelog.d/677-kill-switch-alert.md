- Every paper run that ends `skipped_kill_switch` writes one run-scoped `kill_switch` alert naming the engaged row's source and reason ([#677](https://github.com/josejuarez96/tradepartner/issues/677)).
### Fixed
- `paper run`: a run skipped by an engaged kill switch now writes a `kill_switch` alert (one per run, naming the engaged row's source, reason and the derived causes), per the owner's decision on #644 (#677).

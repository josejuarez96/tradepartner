- #814 plan amendment: the unclaimed run.py follow-ups become a declared chain T87 (#712) → T88 (#592) → T89 (#507), with T86 (#678, marks.py) beside it, so graph shows the contention and claim serialises them
### Changed
- Paper-trading plan: the run.py follow-ups #712, #592 and #507 are plan tasks T87 → T88 → T89 (one declared chain) and #678 is T86 on marks.py, so the fixes land in order instead of as conflicting size:S PRs (#814)

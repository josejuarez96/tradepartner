task: T286 (PR #2283)
- #2280 Plan ticks after merge: the fragment's task line, the fold ticks (PR #2283)
### Changed
- Process: a build PR names its plan task in its fragment (`task: Tn (PR #n)`) and never edits `docs/plans/`; `fragments.py fold` ticks and collapses the line, `team.py` counts a merged fragment as done, `ready_pr.py` enforces both (#2280)

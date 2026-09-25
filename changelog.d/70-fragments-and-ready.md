### Changed
- Process: bookkeeping is a fragment file per PR (`docs/status.d/`, `changelog.d/`, folded by doc-keeper), never an edit to STATUS.md or CHANGELOG.md; `scripts/ready_pr.py` merges main in, resolves append conflicts, runs checks and reviews, waits for CI and marks the PR ready; `/ready-pr` skill (#70)

- #3147 exam ledger is class B on the merge train; ready_pr runs T381b's append-only check on any PR touching it (PR #3206)
### Added
- `docs/research/exam-ledger.toml` is class B (`merge_train.CLASS_B_PATHS`), and `ready_pr.py` refuses a PR whose ledger copy fails `check_append_only` against `origin/main` (#3147)

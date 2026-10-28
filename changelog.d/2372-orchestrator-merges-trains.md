- Rules: the orchestrator builds and merges trains on a green batch without asking; class-B rides only on the owner's word, hold never (#2372).
### Changed
- The orchestrator builds merge trains and runs merge_train.py merge on a green batch without the owner's word (owner, 2026-10-27); class-B PRs ride only with his word for that PR, hold-labelled PRs never.

- CI: pytest shards start with lint/mypy via a small scope job; labeled/unlabeled run CI only for ci:full (#1753)
### Changed
- CI: pytest shards no longer wait for checks-fast; only the ci:full label starts a labeled/unlabeled run, other label toggles cannot cancel a run

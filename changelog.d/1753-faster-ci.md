- CI: pytest shards start with lint/mypy via a small scope job; only the ci:full label starts a labeled run, unlabeled is no trigger (#1753)
### Changed
- CI: pytest shards no longer wait for checks-fast; only the ci:full label starts a labeled run (other labels start a skip-only run that cannot cancel a real run), and unlabeled starts none

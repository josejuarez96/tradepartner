### Changed
- Process: CI runs pytest on a PR only when it touches `src/`, `tests/`, `scripts/`, `.github/`, `pyproject.toml`, `uv.lock` or `.python-version` (`ready_pr.py --tests-needed`); pushes to main still run the full suite (#178).

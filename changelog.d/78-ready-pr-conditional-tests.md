### Changed
- Process: `scripts/ready_pr.py` skips the local pytest run for docs, fragment and process PRs (no `src/`, `tests/`, `scripts/`, `pyproject.toml` or `uv.lock` change); `--tests` forces it, `--no-tests` skips it; CI still runs the full suite on every PR (#78)

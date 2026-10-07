- #1112 CI shards pytest across 4 parallel jobs; `checks` name unchanged (PR #1113)
### Changed
- CI: pytest runs as a 4-way parallel matrix instead of serially inside `checks`; lint/format/mypy moved to a fast `checks-fast` job, and `checks` is now a thin aggregator that still succeeds/fails under the same name (#1112)

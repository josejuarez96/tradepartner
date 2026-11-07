- PR CI runs tests/dashboard only when the PR touches the dashboard; main keeps the full suite (#3221, PR #3223).
### Changed
- CI: a PR's shards skip tests/dashboard unless it touches src/tradepartner/dashboard/, tests/dashboard/ or .streamlit/; main's shard skip now needs a PR run that included them (#3221).

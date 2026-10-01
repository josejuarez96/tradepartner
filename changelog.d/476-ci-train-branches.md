- Merge train T73: CI runs on `train/**` pushes (full suite, one concurrency group per commit, never cancelled) and the `checks` job token is read-only
### Changed
- CI: runs on pushes to `train/**` branches with a per-commit, never-cancelled concurrency group; the `checks` job token is read-only (T73, #476)

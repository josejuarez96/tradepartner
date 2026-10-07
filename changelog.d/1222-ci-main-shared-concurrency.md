- CI: main pushes share one concurrency group (#1222): the run in progress completes, only the newest pending commit runs next, so a merge burst costs one full run
### Changed
- CI: a burst of merges to main runs the full suite once (shared main concurrency group, no in-progress cancel) instead of once per commit (#1222)

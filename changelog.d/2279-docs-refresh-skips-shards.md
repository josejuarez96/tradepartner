- CI skips the pytest shards on a PR refresh whose merge from main brought in only docs markdown, over a head that already passed the full suite (#2279).
### Changed
- CI: a PR refresh that merges only markdown docs from main into a fully tested head no longer re-runs the pytest shards; any other path, a missing green run or any doubt runs the full suite (#2279).

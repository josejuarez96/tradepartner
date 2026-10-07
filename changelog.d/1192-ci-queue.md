- #1192: CI queue relief: draft PRs run checks-fast only and ready_pr labels a draft ci:full for the full sharded run before marking ready; main skips the shards on a tree already fully tested on its PR head; shard weights rebalanced from CI timings.
### Changed
- CI: draft PRs run checks-fast only; ready_pr labels a draft `ci:full` for the full run and marks ready only on its green `checks`; a push to main whose tree already passed every shard on its PR head skips the shards (fails closed); shard weights re-measured on CI (#1192).

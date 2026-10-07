- #1192: CI queue relief: draft PRs run checks-fast only and ready_pr dispatches the full sharded run before marking ready; main skips the shards on a tree already fully tested on its PR head; shard weights rebalanced.
### Changed
- CI: draft PRs run checks-fast only; ready_pr dispatches the full run before marking ready; a push to main whose tree already passed every shard on its PR head skips the shards (fails closed); shard weights rebalanced (#1192).

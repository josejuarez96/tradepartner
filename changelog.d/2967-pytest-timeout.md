- CI: per-test 1200 s pytest-timeout (thread method) and a 45-min shard job limit, so a hung test fails its shard naming the test (#2967)
### Changed
- CI fails a stuck test after 20 min with its stacks dumped, and a shard after 45 min, instead of hanging to the 6-h limit (#2967)

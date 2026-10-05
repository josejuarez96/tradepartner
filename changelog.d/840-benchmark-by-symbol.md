- #840: benchmarks read by symbol, not via the #35 gate; refused when missing, ambiguous or the ticker is reused; bars stay as-of. Owner one-off: `tradepartner backfill-benchmark MTUM` (runbooks/benchmark-backfill.md).
### Fixed
- Every in-sample trial failed on `no equity rows for ['SPY','MTUM']`: benchmarks are now found by symbol over the run window (refused by name when missing, ambiguous or the ticker is reused), a benchmark in the universe is refused, and `tradepartner backfill-benchmark` seeds and backfills a configured benchmark the store lacks (#840).

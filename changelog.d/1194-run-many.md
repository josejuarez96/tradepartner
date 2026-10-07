- T105 (#1194): engine.run_many runs a read group from one read set per step, per-variant failures isolated, SharedReadFailed on a shared read; write_results(detail_level=summary) writes req 13's rows. Follow-up #1197.
### Added
- `engine.run_many`: a sweep read group's variants from one read set per step, each result equal to a separate `engine.run` (`run` is `run_many` with one variant); `results.write_results(..., detail_level="summary")` stores base-level daily equity, rebalance-session equity at the other levels and no weights, with the full trial's metrics (strategy-lab T105).

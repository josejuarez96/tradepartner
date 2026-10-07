- T98b (#1094): look-ahead suites (truncation, prefix, revisions, plan-read timing) and the bt oracle parametrised over month_end, week_end and daily; month_end walk unchanged; asof tie-order follow-up #1099.
### Added
- The look-ahead suites and the `bt` oracle run at every rebalance cadence (`month_end`, `week_end`, `daily`) (strategy-lab T98b).

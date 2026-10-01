- #357 ready_pr's safety gate names `execution/` (was the nonexistent `exec/`), `adapters/alpaca_trading_raw` and the planned `alpaca_broker`; a test fails when a review prefix is missing from the tree unless listed as planned
### Fixed
- ready_pr: order-path PRs (`execution/`, `adapters/alpaca_trading_raw.py`) now require safety-reviewer; a test catches review prefixes that no longer exist (#357)

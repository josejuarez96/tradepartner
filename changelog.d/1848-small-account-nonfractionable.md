- Small-account subshare non-fractionable buys stay open until buy_orders records their skip (#1848)
### Fixed
- Keep small-account whole-share buys below the order minimum open; skip long non-fractionable buys below one share in buy_orders while preserving off and short behavior (#1848 part 1)

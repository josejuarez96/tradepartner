- paper: a rebalance halted between its sells and its buys stays catch_up until F_{i+1} instead of lapsing after max_catch_up_sessions, so a late resume buys the rest (#3131)
### Fixed
- A part-executed rebalance (a fill on one of its orders) no longer lapses to missed after paper.max_catch_up_sessions; it is caught up through the session before the next rebalance's fill session, so a late resume places the unsent buys instead of leaving the sell proceeds in cash for a month (#3131)

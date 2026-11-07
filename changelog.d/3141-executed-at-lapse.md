- paper: a rebalance whose every decision was sent and filled is journaled executed at its lapse, not missed, so the tracking check judges it (daily 10-29) (#3141)
### Fixed
- A traded rebalance left with small open remainders (a notional-cap leftover or a fractional sell, never written off because a sell was in flight at submit) lapsed as missed and the tracking check skipped it; it is now journaled executed at its lapse (#3141)

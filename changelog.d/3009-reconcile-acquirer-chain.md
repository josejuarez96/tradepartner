- #3009: reconcile reads a chained-predecessor merger acquirer as its successor (chain known at close(S-1))
### Fixed
- Reconcile reads a stock merger's chained-predecessor acquirer as the successor the chain known at close(S-1) maps it to, so its receipt and reference price match instead of breaking (#3009).

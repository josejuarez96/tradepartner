- #569 (option b): the wrapper's book prices the batch and ledger strictly; an open order's name with no bar halts only where the reserve or open-sell check reads it (run step 7 follow-up #685)
### Fixed
- A non-terminal order whose name has no bar at close(S-1) no longer halts every wrapper batch; it halts only when the open-buy reserve or the open-sell check reads its price (#569).

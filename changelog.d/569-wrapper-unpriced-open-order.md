- #569 (option b): the wrapper's book prices the batch and ledger strictly; an open order's name with no bar halts only where a number reads it (the reserve, for a batch with buys, before any submit); run step 7 follow-up #685
### Fixed
- A non-terminal order whose name has no bar at close(S-1) no longer halts every wrapper batch: the open-sell check reads only the batch's names, and only a batch with buys halts, before any submit, when the open-buy reserve must price an open quantity buy of that name (#569).

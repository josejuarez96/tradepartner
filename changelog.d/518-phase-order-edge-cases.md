- #518 phase-order edge cases: a trim is capped at holding less residue (owner decision), an untradable name skips before its price read, buys refuse names the sells phase skipped or has in flight, PhaseOrders carry their session
### Fixed
- phases: a trim after a price drop is capped at the holding less its residue instead of halting on sell_within_holding; an untradable sell skips before its reference price is read; buy_orders refuses a name the sells phase skipped or whose sell is in flight; requests_for refuses orders built for another session (#518)

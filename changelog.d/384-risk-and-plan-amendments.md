- Phase 4 T54c: `plan.is_full_exit`; whole-share full exits sell the last share; trim remainder over all orders; `check_phase`: exits exempt from the per-order limit, post-phase weight, one order per (name, side), strict Decimal cash
### Changed
- Execution: risk checks and decision state per the ADR 0010 amendment of 2026-09-30; every sell order by quantity; buy sizing and the cash rule in `Decimal` at the cent through one cost function (#384)

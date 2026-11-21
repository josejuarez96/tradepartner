- #3694: trade_cost refuses (ValueError) a finite notional or share count whose cost overflows to inf; no finite cost changes, digest pins untouched.
### Fixed
- `trade_cost` raises `ValueError` for a finite input whose cost overflows to infinity (a notional above about 1e304) instead of returning `inf`; `buy_notional_after_costs` propagates that refusal instead of sizing the buy at 0. No finite cost changes (#3694).

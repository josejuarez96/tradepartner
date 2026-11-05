- Order cap pre-check: the run's sells plus planned buys are checked against risk.max_orders_per_run before the sell phase journals anything (#2973).
### Fixed
- A run whose sells plus planned buys exceed risk.max_orders_per_run now refuses before the first sell instead of halting after the sells went out (#2973).

- Paper reconcile compares open-order quantity and notional against the journal row, with config tolerances, failing closed (#3336).
### Added
- Paper reconcile now flags a broker open order whose quantity or notional differs from its journal row as open_order_differs (#3336).

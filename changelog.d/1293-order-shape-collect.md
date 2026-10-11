- #1293: collect refuses a non-default order-shape get_order reading (refused_order_shape, before any write; run, _await_sells, halt read, resume), and a non-default own order off the open list is an order_differs reconciliation mismatch.
### Fixed
- Paper collection refuses a broker order reading that is not a market day order (`refused_order_shape`), and reconciliation flags such an order that left the open list (`order_differs`) instead of reconciling it (#1293).

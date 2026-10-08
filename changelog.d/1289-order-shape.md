- T135b (#1289): the wrapper refuses any order shape but a market day us_equity simple order (refused_order_shape, ADR 0015 seam 3): check_phase on session rows, a post-submit halt on broker Orders, reconcile open_order_differs.
### Added
- The order-shape refusal (ADR 0015 seam 3, T135b): `risk.order_shape_violation`; a broker `Order` of another shape from `submit` or `get_order` halts the run after its event is journaled (`refused_order_shape`), `check_phase` refuses such a session `orders` row, and reconciliation reports such an own open order as `open_order_differs`.

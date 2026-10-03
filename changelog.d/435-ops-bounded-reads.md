- #435: execution.ops bounds its chain-view and fills-table reads in SQL (orders and fills by limit, events/outcomes/chain fills by the kept orders); as-of still covers orders past the cap
### Changed
- `store.journal`: `orders_for` and `fills_for` take an optional `limit` (newest first, bounded in SQL), `order_events_for` and `outcomes_for` an optional `client_order_ids`; `execution.ops.page_data` reads only the chain view's kept orders and the newest fills, so its reads scale with `dashboard.page_row_limit`, not the window (#435).

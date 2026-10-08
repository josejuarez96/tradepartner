- T48b done: paper recording resolves the broker facts; quantity_decimals=9, client_order_id_max_length=128, min notional and reconcile tolerances confirmed; cash reflects same-session proceeds (T71 gate); owner questions #1290.
### Added
- Alpaca paper response fixtures (tests/fixtures/alpaca/paper/) and the broker-facts report (docs/research/2026-10-08-alpaca-paper-facts.md) (T48b, #298).
### Changed
- `alpaca.quantity_decimals` defaults to 9 and `alpaca.client_order_id_max_length` to 128 (were unset), from the paper recording and Alpaca's documented limit (T48b, #298).

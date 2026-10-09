- T48c (#1298): `AlpacaBroker` paper adapter (req 2 status table, dedupe through get_order, fills resolved to client_order_id, read-side fields, symbol_for/ticker_for) and `execution.brokers.build_broker`, tested on T48b's recordings.
### Added
- `AlpacaBroker`, the Alpaca paper adapter, and the production broker factory `execution.brokers.build_broker` (T48c, #1298).

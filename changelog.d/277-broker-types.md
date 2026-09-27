### Changed
- Broker interface completed for Phase 4 (spec req 1, #33): `OPEN` is renamed `ACCEPTED`, requests carry `notional` or `quantity` and no price, `cancel` returns `None`, and `get_order`, `open_orders`, `fills(since)`, `account` and `assets` are new; `FakeBroker` takes a `price_of` function (#277).

### Added
- Broker: `FakeBroker` scripting (`script`, `apply`, `complete_cancel`, `lag_fills`, `set_buying_power`, `set_asset(..., from_session=)`, `on_submit`, `calls`) so the Phase 4 wrapper tests can drive every order outcome through the `Broker` methods (T46c, #287)

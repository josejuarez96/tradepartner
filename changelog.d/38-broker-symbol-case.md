### Changed
- Broker value objects (`adapters/broker.py`) canonicalize `symbol` to upper case at construction and reject non-ASCII symbols, so `FakeBroker` nets `aapl` and `AAPL` as one position and future reconciliation compares one case form (separator variants such as `BRK.B`/`BRK-B` are not mapped) (#38).

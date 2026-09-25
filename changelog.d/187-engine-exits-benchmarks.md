### Added
- Phase 3 T37c: delisting and stale exits and the SPY/MTUM buy-and-hold series in `tradepartner.backtest.engine` (`run` reads `benchmark_ids` once at T_0 and `listing_ends` once per step); `tests/backtest/test_engine_exits.py`; `FakeProvider.listing_ends` keeps several listings per security when rows carry `valid_from` (#187).

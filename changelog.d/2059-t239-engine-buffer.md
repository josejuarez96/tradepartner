- T239: the engine applies the momentum buy/hold buffer (#657)
### Changed
- The backtest engine applies `strategy.hold_top_fraction`: with it non-default, the plan's targets keep a held name while it ranks inside the hold share and `n_retained` is reported; every pinned run is byte-identical at the default.

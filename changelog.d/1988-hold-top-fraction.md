- T238: strategy.hold_top_fraction (default 0.0, no buffer), its validation, frozen/family tables and pure buffered_targets; refused at load until T239 (#657, #1988).
### Added
- backtest: the momentum buy/hold buffer key strategy.hold_top_fraction (default 0.0 reproduces today), its validation and tables, and the pure retention rule buffered_targets; load_frozen refuses a non-default value until T239 (#657).

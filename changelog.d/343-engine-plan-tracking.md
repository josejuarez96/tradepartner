- Phase 4 T53: `backtest.engine.plan`/`Plan` public (no behaviour change); `holdout.decide(..., tracking=True)` starts tracking windows after the frozen `holdout.end`, never a spend; `decisions_from` split to T53b (#343)
### Added
- Backtest: public `engine.plan`/`Plan` and `kind=tracking` windows in `holdout.decide` (T53, #343)

- The paper planner applies the frozen strategy.rebalance_band and the hold rule, pinned to the backtest's T209a functions; a hold window is refused until #1832 (#1831).
### Added
- The paper planner's decisions_from takes band= (the frozen strategy.rebalance_band, applied through backtest.portfolio.apply_rebalance_band) and hold= (the HoldBook a weighting = hold window needs via backtest.portfolio.hold_membership); a band of 0 and equal weighting are byte-identical to today's plan rows (#1831).

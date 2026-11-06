- Backtest dividend reads gated on the reviewed duplicate list (#3015): pending duplicates apply once, unexplained equal-amount groups are left unapplied and counted as dropped
### Fixed
- Backtest total-return reads no longer sum a duplicate dividend twice when health has not run: the store provider gates every dividend-including read on the reviewed list, applying a pending duplicate once and leaving an unexplained equal-amount group unapplied, reported as `unexplained_duplicate` (#3015)

- Phases trim cap also subtracts a name's open sells from earlier sessions, and buy_orders skips an ended listing before sizing (#605, #604).
### Fixed
- execution/phases.py: a trim's sell cap subtracts the name's open (non-terminal) sells from earlier sessions, so it passes check_phase's sell_sum_within_holding instead of halting the batch (#605); buy_orders skips a name whose listing ended as skip_delisted before sizing, so it takes no share of the other buys' scaling (#604).

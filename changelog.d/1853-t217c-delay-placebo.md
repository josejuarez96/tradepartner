- T217c done: the engine's plan step reads at the delayed rebalance session and applies the placebo (ADR 0023 rungs 4 and 5); the T217 guard is gone
### Added
- Backtest: strategy.signal_delay_sessions reads every plan input at the latest rebalance session d sessions back, and strategy.placebo shuffles or randomises the scores before construction; defaults unchanged

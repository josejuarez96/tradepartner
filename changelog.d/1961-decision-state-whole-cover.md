- Fix #1961: plan.remainder takes a whole cover (buy + short, no target_notional) through the quantity branch, so decision_state no longer raises
### Fixed
- plan.decision_state no longer raises on a whole cover: a buy + short with no target_notional follows the long full exit's quantity branch (#1961)

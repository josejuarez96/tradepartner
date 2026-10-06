- T95 (#1004): signals.momentum takes the signal anchor (month_end, offset) and cadence; anchor_sessions and check_anchor_feasible (spec req 1(d)) beside it; momentum_12_1 is its month_end case.
### Added
- Momentum at a signal anchor: `month_end` (the Phase 3 rule) or `offset` (the last session on or before T minus k months), with the req 1(d) feasibility check (T95, #1004).

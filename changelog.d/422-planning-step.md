- Phase 4 T63c: `execution/planning.py`, `rebalance_kind` and `plan_rebalance` (plan trial, `engine.plan` at close(T_i), `decisions_from`, `signals`/`decisions`/`paper_plans` in one transaction; `PlanTrialError`) (#422)
### Added
- `execution.planning`: `rebalance_kind`/`due_rebalance`, `plan_rebalance` (one write transaction for the plan trial, `signals`, `decisions` and `paper_plans`; re-use, lagging, `PlanTrialError`, faults and `assets` errors re-raised unchanged) and `reference_prices` (T63c, #422)

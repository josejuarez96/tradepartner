- Phase 4 T53b: `execution.plan.decisions_from` turns a plan into decisions, `signals` rows, buy targets and the live-capital count; the engine's `Plan` gains `members`, `scores`, `excluded_no_history` (#386)
### Added
- `execution.plan.decisions_from`: one decision per held-or-target name (`left_targets`/`left_universe` full exits by quantity, notional trades, `exclude_name`/`keep_name` overrides, `skip_delisted`, `skip_below_minimum`, plan-time `dust`), buy `target_notional`, `signals` rows and `n_orders_below_min_at_live_capital` (T53b, #386)

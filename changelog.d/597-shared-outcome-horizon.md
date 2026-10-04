- Shared outcomes.outcome_horizon between outcomes._horizon and check._order_due_threshold so the due-date rule cannot drift; window._not_ready left unchanged (no due-date filter at all) and reported as an open question (PR #634).
### Changed
- execution.outcomes exposes outcome_horizon(order, decision_rebalance); execution.check._order_due_threshold delegates to it and its own rebalance_before/next_rebalance_session duplicates are removed.

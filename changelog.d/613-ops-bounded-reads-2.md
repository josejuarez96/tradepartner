- #613 (PR pending): execution.ops.page_data's remaining whole-window reads (marks, kill-switch rows, runs, reconciliations, non-terminal orders) bounded in SQL; switch state identical to the unbounded derive.
### Changed
- execution.ops.page_data reads marks only for the latest marked session, at most two kill-switch rows, only the runs switch.derive can draw a cause from, one reconciliation row and one COUNT(*) for open orders, instead of the whole window (#613).

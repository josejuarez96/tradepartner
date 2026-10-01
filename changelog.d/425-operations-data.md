- Phase 4: T66b operations data (execution.ops.page_data) landed, feeding the forthcoming operations page and paper status.
### Added
- execution.ops.page_data: pure read-only summary of a paper window (header dates/stale chip, KPIs, kill-switch state, ranking, fills, per-order chains, alerts, reconciliation status), row-limited by new dashboard.page_row_limit config (ADR 0011).

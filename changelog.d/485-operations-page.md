- T69: Operations page (#485, PR #496) merged into feat/485-operations-page: dashboard/ops_page.py over execution.ops.page_data, ADR 0011 point 3's render_app server-options refusal, config.toml pins.
### Added
- Operations dashboard page (positions, open orders, today's signals, kill-switch chip, ranking hero, fills, chain detail view, alerts, reconciliation status); render_app refuses to render off-localhost or with telemetry on (ADR 0011).

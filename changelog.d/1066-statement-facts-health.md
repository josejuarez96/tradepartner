- T77c (#1066): statement-facts health metrics (coverage, derived share, statement_counts parser) and a dashboard card, behind edgar.statement_facts_enabled.
### Added
- health.py reports statement-facts coverage, derived share and the latest EDGAR run's statement_* counts, with three --check rules (period_days consistency, derived-equals-components, basis) and a dashboard card warning on statement_vintage_late (#660).

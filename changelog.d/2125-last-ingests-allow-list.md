- health: last_ingests counts only real ingest modes, so a repair or action-retract run never hides a failed nightly ingest
### Changed
- health.last_ingests selects the latest run among an allow-list of the ingest's modes (`session`, `backfill`, `import`) instead of a deny-list, so a `repair`, `action_retract`, `holes`, `actions` or `retract` row is left out and never hides a failed nightly ingest's status.

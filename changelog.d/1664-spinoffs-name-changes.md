- T172b+c+e (#1664, PR #1665): Alpaca spin-offs stored in corporate_actions (T64 convention), name changes in a new symbol_changes table (v27); writers, repair rule, --check stamp rules, --actions-backfill from 2020-06-01.
### Added
- Spin-offs and name changes from Alpaca: `spinoff` rows in `corporate_actions`, a `symbol_changes` table (schema 27) with `symbol_changes_as_of`, two `health --check` stamp rules, a `repair-resolution` spin-off rule, and `ingest --source alpaca --actions-backfill --since --through` (floor `alpaca.actions_backfill_floor`, 2020-06-01).

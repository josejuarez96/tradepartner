# Trial Registry

Every backtest or strategy evaluation run is a trial: failures, refusals, parameter tweaks and reruns included. The count is the multiple-testing burden behind the deflated Sharpe. Since Phase 3 the registry lives in the store, not in this file. This table never had a row: no trial ran before the move.

- **Tables** (schema version 3, `REGISTRY_TABLE_NAMES` in `src/tradepartner/store/schema.py`): `hypotheses`, `trials`, `trial_results`, `trial_metrics`, `trial_rebalances`, `trial_equity`, `trial_weights`, `owner_decisions`. Append-only; a trial with no `trial_results` row is `unfinished`.
- **API**: `src/tradepartner/store/registry.py` (register a hypothesis, open a trial, write its outcome and detail rows, owner decisions, N and V for the deflated Sharpe, holdout spends, listing).
- **Reading it**: `tradepartner trials [--hypothesis] [--include-synthetic]` (Phase 3 plan T42), and the trial-registry view on the dashboard (T44).
- **Rules**: [backtest spec](../specs/backtest.md) reqs 8 to 12.

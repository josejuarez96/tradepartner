- ADR 0014 and the strategy-interface plan (#1074): strategies as registered objects; T85e amended into the one dispatch seam with the PAPER_FAMILIES gate; T127 to T130 (generic exclusions and counts, registry, benchmarks, B4 proof).
### Added
- ADR 0014: strategies are registered objects (one dispatch seam keyed on the stored family inside `run_hypothesis` and `plan_rebalance`, one family registry with the momentum fallbacks removed, generic per-strategy exclusions and counts, per-family benchmarks; B4 as strategy #3; six owner decisions of 2026-10-06 recorded).
- Plan `strategy-interface`: T127 (Plan.exclusions, the `trial_rebalance_counts` table, the paper `signals.reason` prefix rule), T128 (`config.FAMILIES`, the tables derive, unknown family fails), T129 (per-family benchmarks, momentum's MTUM keys unchanged), T130 (B4 `combined` through the registry).
### Changed
- Backtest plan T85e amended (#1074): `signal_for` in a new leaf `backtest/strategies.py`, `family` on `engine.run`/`engine.plan` passed from `run_hypothesis` and `_plan_at`, one `Plan.counts` mapping, `PAPER_FAMILIES` so `paper start` refuses `profitability`.

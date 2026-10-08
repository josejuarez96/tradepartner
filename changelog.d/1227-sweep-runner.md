- T107 sweep runner (backtest/lab.py run_sweep) built: read groups via run_many, quiet-interval pauses, budget stop, store-changed by data vintage; #1218 rerun epoch + synthetic planning in lab_queries.
### Added
- `backtest.lab.run_sweep`: the strategy-lab sweep runner (T107), with the rerun epoch and synthetic-run planning in `store.lab_queries` (#1218).

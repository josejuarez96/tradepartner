- T102 sweep file parser and grid (pure) in review (#1096)
### Added
- `backtest/sweep.py`: the strategy-lab sweep file parser and grid, pure: `parse_sweep_file` with every file-level refusal of req 1 as a typed `SweepFileError`, `expand_grid` building each variant's frozen set as `hypothesis.frozen_params` does in canonical `params_sha256` order, `read_groups` and `variant_slug`; fixtures `tests/fixtures/sweeps/` (T102, #1096)

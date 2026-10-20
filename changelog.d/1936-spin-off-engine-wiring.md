- #1936 T211e: the engine's spin-off wiring and the two counts (PR #1948)
### Added
- Backtest: under the frozen `adjust.apply_spin_offs`, the engine reads the two spin-off counts and writes `n_spin_offs_unvalued` and `n_late_spin_offs`; `false` writes nothing (#1894, #1936).

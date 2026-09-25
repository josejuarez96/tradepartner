### Added
- `tradepartner.backtest.holdout`: `decide(window, frozen, flags, reasons, gap_series, prior_spends) -> Decision` (outcomes `run`, `needs_gap`, `refused_window`, `refused_holdout`, `refused_gap`), `window_touches_holdout`, `default_in_sample_window`, `gap_sessions`, and `Frozen.from_hypothesis` reading the registered row, never live `Settings` (T36, #145).

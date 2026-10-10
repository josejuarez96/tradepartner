- #1417 fixed: the single-run backtest path (run_hypothesis -> engine.run) keeps no per-step marking frame, so a long daily backtest's memory stays flat; results and written rows unchanged; only the bt oracle asks for frames (runframes).
### Fixed
- Long single backtests no longer grow without bound: `run_hypothesis` passes `keep_marking_frames=False` to `engine.run` (the `bt` oracle opts back in) (#1417).

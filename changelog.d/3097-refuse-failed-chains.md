- Chain-aware reads refuse a successor chain whose predecessor's Form 25 reads bankruptcy/performance failure, or whose hand-over is past master.chain_window_sessions; engine counts n_refused_chains (#3097)
### Fixed
- Backtest: a successor chain from a bankrupt or failed predecessor, or one handed over beyond `master.chain_window_sessions`, no longer renames the held predecessor 1:1 into the successor; every chain-aware read refuses it, the predecessor exits by the normal delisting path, and the rebalance row counts `n_refused_chains` (#3097).

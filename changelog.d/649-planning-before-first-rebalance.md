- #649: planning.rebalance_kind/due_rebalance return None before a window's first rebalance session (was ValueError); run.py's guard dropped. Alert clock, pre-run-row alerts and staleness source await the owner.
### Fixed
- Paper planning: `rebalance_kind`/`due_rebalance` return None before the window's first rebalance session instead of raising (#649).

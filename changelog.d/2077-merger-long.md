- T249 (#2077): a cash merger on a held long is explained at the stored rate (removal, later cash, the window's one cash check, merger_conflict once); the short half split to T249s (owner 2026-10-20)
### Added
- Reconciliation explains a cash merger on a held long from the stored `mergers` row: the removal and any later cash journal `corporate_action_cash` rows naming the merger id, a due unmet by the window's end halts once, and two conflicting rows halt `merger_conflict` once; new `risk.reconcile_merger_lag_sessions` (default 5) (T249, #2077).

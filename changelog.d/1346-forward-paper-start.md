- T142c (#1346, PR #1347): `paper start` accepts a forward holdout once holdout.start has passed, T_0 inside the holdout; the daily run's tracking rule reads the family's registration day. Historical holdouts unchanged.
### Added
- `paper start` on a forward holdout (ADR 0016 point 4): accepted once `holdout.start` has passed, the window starting at the first month-end on or after it; the daily run's plan trial accepts that window (T142c, #1346).

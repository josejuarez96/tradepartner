- T166f in review: the in-window gate, `stamp_repair.in_window_change_allowed` and the read-only pre-check (`store/restamp_precheck.py`, `scripts/restamp_precheck.py` writing precheck.<release>.json); T166d and T166e build on it (#1497).
### Added
- The in-window restamp gate (#1497, T166f): `stamp_repair.in_window_change_allowed`, and `scripts/restamp_precheck.py`, a read-only pre-check listing every paper read instant and the in-window rows a correction could move.

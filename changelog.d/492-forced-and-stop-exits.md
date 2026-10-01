- T63e forced and stop exits, pure: forced_exits (delisted, untargeted_receipt, untradable closed), reattempt_exits, stop_exits in execution/exits.py ([PR #493](https://github.com/josejuarez96/tradepartner/pull/493))
### Added
- Forced and stop exits, pure (T63e): `execution.exits` decides delisted and spin-off receipt exits, re-attempts open forced exits, and sizes `window_stop` exits to the holding minus its residue with the whole-share floor

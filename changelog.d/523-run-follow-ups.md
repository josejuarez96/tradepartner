- #523/#549 run.py follow-ups: drawdown checked on every back-filled mark, StepContext drops the raw broker, run-level kill/lapse/clock/step-8 tests and T63d's leftover submit-window and same-session exit cases.
### Changed
- The run's StepContext no longer carries the raw broker; later steps reach it only through the gate and assets_read (#523).
### Fixed
- The tracking run's drawdown check now covers every session the run marks, so a crossing on a back-filled session engages the switch (#523).

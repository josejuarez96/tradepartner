- Phase 4 T65: `execution/report.py`'s `compare_months`, the req 10 monthly decomposition (raw, dividend, fill-timing, residual, residue, modelled cost) and the frozen `tracking_k`/`tracking_rule` check (#424)
### Added
- Phase 4 T65: `execution.report.compare_months(window, trial, journal, actions, prices, stop_session)` computes the req 10 tracking-comparison terms per month and the frozen-rule check (#424)

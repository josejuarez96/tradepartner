- T136 (TE4, ADR 0015 seam 4): the paper path takes the window's frozen cadence explicitly; holdout.first_tracking_session follows the cadence (was always a month end). paper start still refuses non-month_end.
### Changed
- The paper path (run, planning, plan, marks, outcomes, check, report, window) reads the window's hypothesis's frozen `schedule.rebalance_cadence` and passes it to every rebalance-session call; `first_tracking_session` now returns the first rebalance session of the hypothesis's cadence after `holdout.end` (T136, ADR 0015 seam 4).

- T151 (ADR 0017 part C): paper start accepts month_end, week_end and daily; report/check read the window's cadence (#1286); paper.min_rebalances per cadence {6, 13, 63}; a daily rebalance left pending lapses on F_{i+1}.
### Changed
- `paper start` accepts every cadence (`refused_cadence` removed): the hypothesis's frozen `schedule.rebalance_cadence` drives the holdout-end check, T_0 and the forward first session; `paper report` and `paper check` read the tracking trial's periods and fill sessions at the window's cadence (#1286); `paper.min_rebalances` is the per-cadence table `{month_end: 6, week_end: 13, daily: 63}`, the window freezing its cadence's entry as the scalar (T151, ADR 0017 part C).
### Fixed
- A rebalance left pending lapses `missed` once the next rebalance's fill session arrives, even inside `paper.max_catch_up_sessions` (at `daily`, on F_{i+1}), instead of staying pending after `due_rebalance` had moved on (T151, ADR 0017 section 2).

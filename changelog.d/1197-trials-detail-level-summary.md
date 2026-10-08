- #1197 done: open_trial takes detail_level; sweep variants open at lab.sweep_detail_level so trials.detail_level reads summary; write_results refuses a level other than the opened one.
### Fixed
- A sweep variant written at summary now records trials.detail_level = summary, so the backtest page no longer looks for weights it never stored (#1197).

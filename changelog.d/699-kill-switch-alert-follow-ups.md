- #699: the run's kill_switch alert reads switch.derive's new engaged_row, not its own row scan; tests for secret masking in it, a same-run drawdown alert, and a pin of alert-write-error -> failed (owner question).
### Changed
- The paper run's `kill_switch` alert names `switch.derive`'s `engaged_row` (new on `SwitchState`) as the latest engaged row, so the alert and the derived state cannot disagree (#699).

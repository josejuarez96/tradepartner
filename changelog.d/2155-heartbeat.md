- Off-Mac heartbeat for paper run and ingest, plus a halt runbook ([#2155](https://github.com/josejuarez96/tradepartner/issues/2155)); owner sets up the checks
### Added
- Optional dead-man's-switch heartbeat: `paper run` and `ingest` ping `ALERT_HEARTBEAT_URL_PAPER` / `ALERT_HEARTBEAT_URL_INGEST` on completion (`/fail` on a non-zero outcome), best effort, URL scrubbed; halt card and setup steps in the scheduling runbook (#2155)

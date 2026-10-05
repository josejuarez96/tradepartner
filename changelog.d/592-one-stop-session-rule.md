- T88 (#592, PR #861): one stop-session rule, plan.stop_session, shared by run (re-export), report and check; the private copies are gone.
### Changed
- Paper trading: run, report and check read the stop session through one function, plan.stop_session (#592).

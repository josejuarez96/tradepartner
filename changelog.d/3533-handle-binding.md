- A TrialHandle now binds one run: window, params hash and holdout bound are checked and a handle is single-use (#3533, PR #3616)
### Fixed
- engine.run refuses a reused, other-window, other-params or in-sample-over-holdout trial handle before any provider call (#3533)

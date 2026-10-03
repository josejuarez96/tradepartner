- Drawdown crash-gap check widened, unspent_cash alert deduped and skipped in stop runs, stop-run lookahead case added (#560, #563, #598; PR #TBD).
### Fixed
- execution: _drawdown checks every marked session after the last release, not just this run's; back-filled crossings are labeled (#560).
- execution: _unspent_cash writes one alert per call site naming every rebalance over the bound, and skips step 7b's check in stop runs (#563).
- tests: stop-run outcomes/stop_exits added to the paper-invariance lookahead check; stop-run mark and missed-row gaps pinned (#598).

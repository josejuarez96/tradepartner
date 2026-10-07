- quant-auditor runs targeted tests only and checks new tests fail on the merge-base; CI runs the suites (#1161)
### Changed
- quant-auditor agent: targeted tests only (changed files plus new tests against the merge-base); never the full suite or tests/lookahead/ whole (#1161)

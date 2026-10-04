- #741 closed by owner decision (a): a halt writes exactly one halted alert and no kill_switch alert (spec req 4/11); the stale xfail is now a passing test asserting that.
### Changed
- test(execution): the xfail expecting a kill_switch alert on a halt now asserts spec req 4/11, one halted alert and no kill_switch alert (#741).

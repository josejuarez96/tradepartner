task: T371 (PR #2881)
- T371: phase (i′), pre-open buys from cash already held on V1 sessions, the negative_cash halt, and the sandbox window before the open (PR #2881)
### Added
- Phase (i′), the open tranche: on a V1 session of a book in `paper.execution`, long notional buys go before the open from cash already held; `negative_cash` halts; the sandbox book's window opens before the open (#2842, T371).

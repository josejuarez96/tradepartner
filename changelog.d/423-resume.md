- Phase 4 T61b: `execution/resume.py`, `paper resume` (settle pending, collect, rejection-cap refusal per #374, lag bound or synthetic residual fills, reconcile, release with the last mark's equity as peak) (#423)
### Added
- Execution: `resume` (`paper resume`): settles pending orders, collects, refuses on a rejection-cap verdict, the lag bound or a failing reconciliation, journals synthetic residual fills with `--accept-broker-fills`, and releases the kill switch (T61b, #423, #374)

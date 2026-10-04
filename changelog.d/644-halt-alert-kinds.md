- Halt writes one alert with a fault-specific kind (reconciliation/rejection_cap/skip_cap), not a generic halted (#644, PR #667).
### Fixed
- wrapper.halt() maps ReconciliationError/RejectionCapError/SkipCapError to their own alert kind instead of halted; stale_data and the generic halted fallback are unchanged.

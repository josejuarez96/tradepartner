- Hardened ready_pr CI, checklist, and review staleness checks
### Fixed
- ready_pr now waits for a newer full CI run after a cancelled run's failed aggregator, rejects unclosed fences, and detects non-ASCII paths in review staleness checks.

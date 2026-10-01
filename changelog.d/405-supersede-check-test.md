- #405 the bad-superseded-pointer test's chained and self cases use live-looking `broker_status` targets, so deleting `s.superseded_by IS NOT NULL` from `fills_for`'s check now fails them
### Fixed
- Tests: `fills_for`'s chained and self-pointing superseded checks are now exercised; a mutation dropping the clause fails two cases (#405)

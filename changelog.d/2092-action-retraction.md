- T262 (#2092): `action-retract` withdraws a corporate action the source withdrew, as a point-in-time cancel; ingest keeps it, so a withdrawn split stops halting reconcile from the next session.
### Added
- `tradepartner action-retract`: withdraw (or reinstate) one stored corporate action as a `cancelled` revision known at the clock, never back-dated; ingest no longer revives an owner's withdrawal the source still serves (#2092, T262).

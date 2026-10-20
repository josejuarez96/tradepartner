- Owner upkeep runbook (#1938, #1934, #1759): update-the-system with store backup and restore, stale migration section fixed, shakedown-span help and abandon banner fixed, one wake replaced by no sleep on power.
### Added
- docs/runbooks/update-the-system.md: the owner's update routine, and backup and restore for a store damaged outside a release (#1938).
### Changed
- Scheduling runbook: schema-migration section points to the update routine; sleep section says keep the Mac on power with sleep off, since one wake cannot cover both jobs (#1759).
### Fixed
- decision shakedown-span --reason help no longer says to name a live strategy; stale 'abandon not yet built' banner removed (#1934).

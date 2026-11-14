- #3390 (PR #3399): a notes-only issuer's first note takes a suffixed id, not the main id (BUILD_RULES_VERSION 11); needs a master rebuild
### Added
- A note class never takes an issuer's main id, so a master-retract of the main id's rows is not re-derived by the next EDGAR write (#3390).

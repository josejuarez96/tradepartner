- #1448: a summary-level sweep keeps no position values and no weight rows (engine.run_many keep_detail=False from the lab); standalone and full-detail paths unchanged, stored rows identical.
### Fixed
- Summary-level sweeps no longer hold every step's position values and every cost level's weight rows in memory (#1448).

- #3672: a fresh store gets the lab tables (schema 50); a store created without them at 16 to 49 is repaired on migration, its hypotheses marked pre-lab.
### Fixed
- A store created fresh never got the eight strategy-lab tables, so every lab command raised `LabNotInitialised` there. `init_schema` now applies the lab schema to a fresh store, and schema version 50 repairs a store created without it at version 16 to 49 (existing hypotheses are marked pre-lab, as the version-16 migration did; a store that has the lab tables only gains its version row). (#3672)

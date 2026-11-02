task: T366 (PR #2849)
- T366: new statement names stay out of trial vintages (PR #2849, awaiting review)
### Added
- Trial data vintage counts only the eight pre-#2826 statement_facts names, so a backfill of new names stales no trial; source scan keeps readers on named held constants (#2826).

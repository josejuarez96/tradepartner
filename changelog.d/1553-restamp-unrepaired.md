- #1553: `repair_stamps.py restamp-unrepaired --from <unrepaired.csv.gz>` re-stamps an earlier release's unrepaired list filing by filing from each SGML ACCEPTANCE-DATETIME (release restamp-1382a3 next, owner merges).
### Added
- `scripts/repair_stamps.py restamp-unrepaired`: re-stamps every record of an earlier release's unrepaired list from its own SGML acceptance through step 4's per-filing path, with the dry-run count, request cap, backup, journal and --resume of `run`; unreadable and pre-2003 headers stay listed with their reason (#1553).

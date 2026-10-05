- master-retract keep-list (#922): `master.keep_successors` ids are never proposed for retraction, listed as kept in the dry run, and not recorded by ingest's check, so health passes; default empty, malformed id refuses the config.
### Added
- `master.keep_successors`: owner-accepted successor ids that `master-retract` never proposes and health's `underived_master_rows` does not fail on (#922; MTCH `0000891103@2020-08-10`, #828).

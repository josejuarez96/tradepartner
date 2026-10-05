- #859 master-retract: false securities/listings rows (WSC 0001647088@2020-08-10) are retracted as revisions known at the run (schema 11); health underived_master_rows; spec amended
### Added
- `tradepartner master-retract` (#859): lists every stored securities/listings row the current master rules no longer derive (dry run) and, with `--apply --expect-rows N`, retracts them as point-in-time revisions (`retracted`, schema version 11); EDGAR ingests record the set in `master_underived` and `health --check` fails `underived_master_rows` while any is live.

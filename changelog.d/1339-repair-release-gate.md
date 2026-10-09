- T140c: repair-resolution, master-retract --apply and the new repair-bars (one security's Alpaca bars and actions on a session range) refuse to write outside the open data release named by --release (#1339).
### Added
- `tradepartner repair-bars --security --from --to --release [--dry-run]` deletes one security's Alpaca bars and corporate actions, every revision, on a session range, in one recorded transaction; `repair-resolution` and `master-retract --apply` now take `--release` and refuse to write without that open data release (T140c, #1339).

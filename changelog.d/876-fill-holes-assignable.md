- #876: ingest --fill-holes lists and fetches only holes the store resolver can assign (dropped ones counted per reason) and takes --security ID[,ID] for a targeted refill; spec req 9 amended.
### Added
- `--security ID[,ID...]` (repeatable) on `ingest --backfill --fill-holes` limits the fill to the named securities; a named id with no hole fetched is listed with why (#876).
### Fixed
- `ingest --backfill --fill-holes` no longer lists or fetches holes no fetched bar can land on (the resolver assigns the id no session, or only a non-Alpaca ticker); the dry run and each month's run row count them per reason (#876).

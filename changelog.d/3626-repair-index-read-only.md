- #3626: the repair commands' stored filing-index handle is read-only for the fetch pass, so readers are no longer locked out while EDGAR is fetched.
### Fixed
- ingest --restamp / --rebuild-classifications and master-retract held the store read-write for the whole EDGAR fetch pass; the stored-index handle is now read-only there, so the dashboard, paper run and other readers can open the store (#3626).

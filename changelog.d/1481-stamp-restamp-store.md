- T166b (#1481): `ingest --source edgar --restamp-from <manifest>` moves the stored EDGAR filing stamps a #1382 stamps-repair manifest repairs (UPDATE in one transaction, refused CIKs listed, open paper window aborts); T166c runs it.
### Added
- `ingest --source edgar --restamp-from <manifest>`: moves stored EDGAR filing stamps per a #1382 stamps-repair manifest, never delete-and-reinsert; date-crossing CIKs refused and listed, a change in an open paper window aborts (#1481).

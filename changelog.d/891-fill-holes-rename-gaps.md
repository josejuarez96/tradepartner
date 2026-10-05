- #891: --fill-holes also refetches a rename lead's gap inside a month with stored bars (FB->META June/July 2022), and counts lead-only months as assignable; spec req 9 amendment, class B.
### Fixed
- `ingest --backfill --fill-holes` refetches a rename gap (#843 lead) that starts or ends inside a month the store already has bars in, and no longer drops a month only the rename lead can fill as unassigned (#891).

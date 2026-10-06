- T116b first-span lead backfill (#974, #988): every month fetches each led security under its first-span lead (not OTC, never in the staleness denominator); fill-holes lists its empty months and the partial month as a lead gap.
### Added
- Backfill fetches the first-span lead's months (#974): `_window_names` takes the store's resolver and adds every security its first-span lead assigns a session of the month under an Alpaca symbol, when its first listing row is fetched; never in the staleness denominator; `--fill-holes` calls either lead's partial month a lead gap.

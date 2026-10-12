- T182 built: ingest --source edgar --rebuild-classifications deletes the covered securities' classifications and rewrites the build's rows at their own known_at (PR open)
### Added
- ingest --source edgar --rebuild-classifications (#1541, T182): rebuild classifications in the EDGAR chunk's transaction; refused with --source alpaca or --backfill; a dry run writes nothing

- ingest --source edgar --sec-forms-only runs the SEC forms backfill alone, one form345 quarter in memory at a time (#2624, PR #2634)
### Added
- `ingest --source edgar --sec-forms-only`: the SEC ownership forms backfill without the full EDGAR pass; the Form 4 bulk step holds one quarter at a time (#2624)

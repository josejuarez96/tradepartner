- #3644: the EDGAR filing-date clock rule lives in the leaf adapters/edgar_clock.py; importing the EDGAR adapter no longer loads health, ingest or backfill.
### Changed
- EDGAR_FILING_DATE_CUTOFF and clock_suspect moved from health.py to the new leaf module adapters/edgar_clock.py (health re-exports both), so adapters/edgar_source.py no longer imports health and the adapter-to-ingest import cycle is gone (#3644).

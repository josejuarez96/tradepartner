- EDGAR: an accession a post-settle fetch lacks is re-asked for edgar.unstampable_retry_days (30) before it is cached unstampable, so a late SEC stamp reaches filing_index on both the full and incremental paths (#2865, PR #3038)
### Fixed
- EDGAR: a stamp SEC serves after its quarter settled is no longer lost; the unstampable decision waits `edgar.unstampable_retry_days` (default 30) past the settle (#2865)

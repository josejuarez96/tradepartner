- #1382 guard: new EDGAR stamps never come from submissions.zip (edgar.bulk_stamps_enabled, default off); stamping goes per CIK; the refresh writes items only. The per-CIK repair is a plan task.
### Fixed
- EDGAR stamps are no longer taken from SEC's bulk submissions.zip, whose acceptance times are moved by New York's UTC offset (early for many CIKs); `edgar.bulk_stamps_enabled` (default off) re-enables it (#1382).

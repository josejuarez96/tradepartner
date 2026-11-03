- EDGAR filing-name fetches run on a bounded pool (edgar.filename_fetch_workers, default 4) under the one 9 req/s limiter; answer and cache identical to the sequential fetch, ~2.6x on the offline probe (#2932).
### Added
- `edgar.filename_fetch_workers` (default 4, 1 to 8): `filename_listings` prefetches one CIK's filing-name index and document requests on that many threads, all behind `edgar_raw`'s one process-wide limiter; results are taken in stamp order, so the answer, the cache file and the counts equal the sequential fetch's (#2932).

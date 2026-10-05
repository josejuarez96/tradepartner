- #761: EDGAR bulk/FSN zips are checked before they replace the cached copy; a persistent 403 is waited out once per run, not per request; a 429 past its backoff is waited out as SEC's block.
### Fixed
- EDGAR: a corrupt streamed zip no longer overwrites the previous good `submissions.zip`/`companyfacts.zip`/FSN zip; a persistent 403 costs one `edgar.rate_limit_wait_seconds` wait per run instead of one per request; a 429 still returned after its backoff attempts is waited out once as SEC's rate-limit block instead of failing the run (#761).

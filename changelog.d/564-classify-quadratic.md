- #564 (PR #565): build_classifications makes one ordered pass per company (5k-filing issuer 7.4 s to 0.01 s, output identical to the old code); _ingest_filings skips its redundant fetch-pass build after a prefetch.
### Fixed
- build_classifications was quadratic in filings per company and stalled the backfill for 8+ hours; it now folds each CIK's evidence once and evaluates each class only where its inputs change, with rows identical to before; _ingest_filings no longer rebuilds a prefetched source's fetch pass (#564).

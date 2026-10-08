- #1232 done: registry.write_result fails a run 'store changed during run' by the data vintage at the trial's cutoff (req 9 as amended); a post-cutoff ingest fails nothing; no recorded vintage keeps the Phase 3 rule.
### Fixed
- registry.write_result applies the amended req-9 data-vintage rule, so a nightly ingest of a later session no longer fails a run or a sweep variant (#1232).

- #1060 items 2-3: statement_malformed counts read-time end-after-acceptance drops (late stamps); parse counts skip co-registrant accessions. Item 1 (cache key) waits for the owner.
### Fixed
- The EDGAR statement-facts counts: `statement_malformed` now counts entries a late stamp drops for ending after acceptance, and `statement_non_usd`/`statement_malformed` no longer count co-registrant accessions' entries (#1060).

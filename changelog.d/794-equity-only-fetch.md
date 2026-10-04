- #794: backfill and daily ingest fetch only listings on universe.exchanges whose security is common, a configured universe.security_types type (any classification revision) or not yet classified, plus benchmarks and the reference symbol; notes, preferreds and OTC listings are no longer requested.
### Fixed
- Backfill and ingest no longer request notes, preferreds or OTC listings from the price source; they fetch only names the universe or the staleness counts can use (common or `universe.security_types` on `universe.exchanges`, unclassified names, benchmarks, the reference symbol) (#794).

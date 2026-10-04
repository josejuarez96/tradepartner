- #794: backfill and daily ingest fetch only common equity on universe.exchanges plus benchmarks and the reference symbol (the _counted predicate from #788); notes, preferreds and OTC listings are no longer requested.
### Fixed
- Backfill and ingest no longer request notes, preferreds or OTC listings from the price source; they fetch only names the staleness counts can use, plus the reference symbol (#794).

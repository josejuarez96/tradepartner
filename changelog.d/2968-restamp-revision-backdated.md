- #2968: restamp moves stamped one tick before the run; moved fact accessions can be revised in the same run
### Fixed
- A restamp run whose moved fact accession also needs a revision no longer fails as back-dated: the moves are stamped one microsecond before the run, so the run's writes are a later ingest of the accession (#2968).

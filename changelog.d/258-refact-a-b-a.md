- #258 fact ingest: an accession re-dated A→B→A serves A again; the writer re-inserts the builder's pairs missing from the accession's latest ingest at ingested_at (owner option a), never rewriting stored rows
### Fixed
- Fact ingest re-inserts a filing accession's current date/value pairs at `ingested_at` when its latest ingest no longer holds them, so a fact re-dated A→B→A serves A again without changing earlier as-of reads (#258).

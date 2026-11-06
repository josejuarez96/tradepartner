- filing_index stores list_position (schema 46); accession_listing_order reads by it, not rowid
### Added
- Schema 46: nullable list_position on filing_index and filing_index_pending, written by T319's incremental read; filing_index.accession_listing_order.

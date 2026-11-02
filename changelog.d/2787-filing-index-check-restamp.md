task: T318b (PR #2800)
- T318b: filing_index --check rules and the (cik, accession) restamp match are built; waiting on review
### Added
- health --check gains six filing_index revision rules; the stamps-repair restamp moves every revision of a matched accession by (cik, accession) and refuses one whose new acceptance is at or after a revision's known_at

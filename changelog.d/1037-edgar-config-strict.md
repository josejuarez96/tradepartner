- #1037: EdgarConfig refuses unknown keys (extra=forbid) and empty edgar.statement_tags / statement_forms / statement_units or an empty fallback list.
### Fixed
- A mistyped edgar.* key now fails config loading instead of being silently ignored, and an empty statement_tags, statement_forms, statement_units or fallback list is refused (#1037).

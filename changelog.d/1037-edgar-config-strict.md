- #1037: EdgarConfig refuses unknown keys (extra=forbid) and empty edgar.statement_tags / statement_forms / statement_units or an empty fallback list; Settings hides input values in validation errors.
### Fixed
- A mistyped edgar.* key now fails config loading instead of being silently ignored, and an empty statement_tags, statement_forms, statement_units or fallback list is refused; a config validation error no longer echoes the offending value, which may be a secret (#1037).

- #1624: the FSN cache JSON round-trips `FsnFiling.symbol_only`; `cover_12b_exchanges` takes a required byte cap
### Added
- EDGAR FSN cache: the per-CIK JSON writes and reads `FsnFiling.symbol_only` (#1624), so a filing reloaded from a cached period carries the symbol-only tag its parse recorded. `cover_12b_exchanges` requires its byte cap (`edgar.filename_document_max_bytes`) and takes no `None`: a primary document is never read unbounded.

- #1624: the FSN cache JSON round-trips `FsnFiling.symbol_only`; `cover_12b_exchanges` takes a required byte cap
### Added
- EDGAR FSN cache: the per-CIK JSON writes and reads `FsnFiling.symbol_only` (#1624), so a filing reloaded from a cached period carries the symbol-only tag its parse recorded. An entry laid out before the field recorded none, and a cached value that is not a list of strings refuses the entry rather than reading as the letters of one symbol or as symbols that are not strings.
- `cover_12b_exchanges` requires its byte cap (`edgar.filename_document_max_bytes`) and takes no `None`: a primary document is never read unbounded.

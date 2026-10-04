- #819 resolver: a span ends at its own delisting (a reused ticker never prices a dead security), a same-day ticker typo keeps the held ticker; `repair-resolution` deletes misattributed Alpaca rows (merges after #820).
### Added
- `tradepartner repair-resolution [--dry-run]` (#819): deletes, in one recorded transaction, every Alpaca bar and action the current resolver does not assign to its security.
### Fixed
- Price resolution (#819): a span ends at its security's own delisting unless a later row of the same ticker follows, so a reused ticker (EGLE, ELOX, OPI, MRLN, IAC, ...) never prices a delisted security; a same-day two-ticker cover page keeps the ticker the security held (FF, TMPM, CLRC).

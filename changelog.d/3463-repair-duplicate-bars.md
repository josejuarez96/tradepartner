- repair-resolution: a stale bar key whose owner already holds the same bar (volume, OHLC within release_move_price_tolerance) is a DUPLICATE line, not an orphan, so --allow-orphans counts only real losses (#3463)
### Fixed
- repair-resolution no longer counts as orphans the bar keys an owner already holds as its own identical bar; the dry run lists them as 'duplicate of <owner>' (#3463)

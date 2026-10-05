- Strategy-lab T99 (#944): adjusted_prices_as_of and DataProvider.adjusted_prices take sessions_from, a bound applied after the as-of read (factors and known_at cut unchanged); T98's signal read consumes it.
### Added
- `adjusted_prices_as_of(..., sessions_from=)` and `DataProvider.adjusted_prices(..., sessions_from=)`: drop bars before a session from the returned frame after the as-of selection, so adjustment factors and the known_at cut are unchanged (strategy-lab T99, #944).

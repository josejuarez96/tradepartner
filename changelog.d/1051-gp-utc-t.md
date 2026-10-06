- #1051: signals.gross_profitability reads a tz-aware non-UTC t as the same UTC instant (was a polars SchemaError).
### Fixed
- gross_profitability accepts a tz-aware t in any zone; it no longer fails comparing it to the UTC known_at column (#1051).

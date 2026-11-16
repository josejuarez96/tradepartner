- #3339: Backtest marking bounds are computed once per step; fixture trial output stays byte-identical.
### Changed
- Backtest marking reads reuse one bound per step and cache each held formation anchor by session (#3339).

- #1305 fixed: the universe's bar read is bounded to the rules' windows and built once per step (the gap reuses it); outputs byte-identical; a plan read at 2023-11 drops from ~29 s to ~4 s on a store copy.
### Fixed
- Backtest step time no longer grows with the step's calendar date: `universe_as_of` reads bars only from its rules' windows (`prices_as_of(sessions_from=...)`), and the backtest provider builds one universe per step that the survivorship gap reuses (#1305).

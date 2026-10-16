- T192 (#1696): the resolver follows stored symbol_changes where listings lag them (a denied change is refused); a later rename contests only a span still live then (PARA). Census on #1696.
### Added
- Price resolver follows Alpaca symbol changes stored in `symbol_changes` where the master's listing rows lag them, ending the old symbol's span and starting the new one on the change's process date; a change the resolver then denies is refused (#1696).
### Changed
- A later rename into a ticker contests only a span still live when the renamer's span starts; an ended span is asked with `asof` and never falls back to a request without it (#1696).
